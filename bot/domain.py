from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NO_USERNAME = "нет никнейма"
SKIPPED = "—"
FREE_TEXT_LIMIT = 500
WITHDRAWAL_PHRASE = "отзываю согласие"
STALE_ALERT = "Эта анкета уже завершена. Отправьте /start, чтобы начать заново"
OUT_OF_FLOW = "Чтобы начать подбор специалиста, отправьте /start"
EMPTY_DONE_ALERT = "Выберите хотя бы один вариант"
PAGE_SIZE = 5

# Экраны, на которых есть отдельный нижний ряд «Назад».
BACK_SCREENS = frozenset({"S1", "S2", "S3", "S4", "S4a", "S5", "S6"})
TZ_FORMATS = frozenset({"f_online", "f_any"})

FUNNEL_SCREENS = (
    "S0",
    "P1",
    "S1",
    "S2",
    "S3",
    "S4",
    "S4a",
    "S5",
    "S6",
    "S6b",
    "S7",
    "S7b",
    "S8",
)

# Куда должен дойти человек, чтобы экран не считался отвалом.
NEXT_SCREENS: dict[str, frozenset[str]] = {
    "S0": frozenset({"S1"}),
    "P1": frozenset({"S1"}),
    "S1": frozenset({"S2"}),
    "S2": frozenset({"S3"}),
    "S3": frozenset({"S4"}),
    "S4": frozenset({"S4a", "S5"}),
    "S4a": frozenset({"S5"}),
    "S5": frozenset({"S6"}),
    "S6": frozenset({"S6b"}),
    "S6b": frozenset({"S7", "S7b"}),
    "S7": frozenset({"S8"}),
    "S7b": frozenset({"S8"}),
    "S8": frozenset(),
}

SCREEN_TITLES = {
    "S0": "S0 · приветствие",
    "P1": "P1 · текст согласия",
    "S1": "S1 · запрос",
    "S2": "S2 · уточнение",
    "S3": "S3 · опыт терапии",
    "S4": "S4 · формат",
    "S4a": "S4a · часовой пояс",
    "S5": "S5 · бюджет",
    "S6": "S6 · время",
    "S6b": "S6b · проверка",
    "S7": "S7 · отправка",
    "S7b": "S7b · нет никнейма",
    "S8": "S8 · финал",
}

STATUSES = {
    "new": "новая",
    "in_progress": "в работе",
    "closed": "закрыта",
}

STATUS_FILTERS = (
    ("a", "Все"),
    ("n", "Новые"),
    ("w", "В работе"),
    ("c", "Закрытые"),
)
PERIOD_FILTERS = (
    ("a", "Все даты"),
    ("d", "Сегодня"),
    ("w", "7 дней"),
    ("m", "30 дней"),
)


@dataclass(frozen=True)
class Option:
    code: str
    label: str
    card: str


PROBLEMS = (
    Option("c_anx", "Тревога и панические атаки", "Тревога и панические атаки"),
    Option("c_ocd", "Навязчивые мысли и ритуалы", "Навязчивые мысли и ритуалы"),
    Option("c_mood", "Подавленность и апатия", "Подавленность и апатия"),
    Option("c_past", "Тяжёлый опыт в прошлом", "Тяжёлый опыт в прошлом"),
    Option("c_rel", "Отношения", "Отношения"),
    Option("c_self", "Самооценка и границы", "Самооценка и границы"),
    Option("c_work", "Работа и выгорание", "Работа и выгорание"),
    Option("c_other", "Другое", "Другое"),
)
EXPERIENCE = (
    Option("e_first", "Впервые", "Впервые"),
    Option("e_fail", "Был, не помогло", "Был, не помогло"),
    Option("e_ok", "Был, помогло", "Был, помогло"),
    Option("e_psy", "Наблюдаюсь у психиатра", "Наблюдаюсь у психиатра"),
)
FORMATS = (
    Option("f_online", "Онлайн", "онлайн"),
    Option("f_offline", "Лично в офисе", "лично в офисе"),
    Option("f_any", "Не принципиально", "не принципиально"),
)
TIMEZONES = (
    Option("tz2", "UTC+2 (Калининград)", "UTC+2"),
    Option("tz3", "UTC+3 (Москва)", "UTC+3"),
    Option("tz45", "UTC+4 / +5", "UTC+4 / +5"),
    Option("tz67", "UTC+6 / +7", "UTC+6 / +7"),
    Option("tz8", "UTC+8 и восточнее", "UTC+8 и восточнее"),
    Option("tz_other", "Другой / за рубежом", "другой"),
)
BUDGETS = (
    Option("p1", "До 3 000 ₽", "До 3 000 ₽"),
    Option("p2", "3 000–5 000 ₽", "3 000–5 000 ₽"),
    Option("p3", "5 000–8 000 ₽", "5 000–8 000 ₽"),
    Option("p4", "От 8 000 ₽", "От 8 000 ₽"),
    Option("p0", "Пока не знаю", "Пока не знаю"),
)
SLOTS = (
    Option("t_morn", "Будни до 12:00", "будни до 12:00"),
    Option("t_day", "Будни 12:00–18:00", "будни 12:00–18:00"),
    Option("t_eve", "Будни после 18:00", "будни после 18:00"),
    Option("t_week", "Выходные", "выходные"),
)

_BY_CODE = {
    item.code: item
    for group in (PROBLEMS, EXPERIENCE, FORMATS, TIMEZONES, BUDGETS, SLOTS)
    for item in group
}
PROBLEM_CODES = frozenset(item.code for item in PROBLEMS)
EXPERIENCE_CODES = frozenset(item.code for item in EXPERIENCE)
FORMAT_CODES = frozenset(item.code for item in FORMATS)
TIMEZONE_CODES = frozenset(item.code for item in TIMEZONES)
BUDGET_CODES = frozenset(item.code for item in BUDGETS)
SLOT_CODES = frozenset(item.code for item in SLOTS)
SLOT_ORDER = [item.code for item in SLOTS]


def label_of(code: str | None, *, card: bool = False) -> str:
    if not code or code == SKIPPED:
        return SKIPPED
    item = _BY_CODE.get(code)
    if item is None:
        return code
    return item.card if card else item.label


def mark(label: str, selected: bool) -> str:
    return f"✅ {label}" if selected else label


@dataclass
class Answers:
    problem: str | None = None
    free_text: str | None = None
    therapy_exp: str | None = None
    format: str | None = None
    tz: str | None = None
    budget: str | None = None
    slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "problem": self.problem,
            "free_text": self.free_text,
            "therapy_exp": self.therapy_exp,
            "format": self.format,
            "tz": self.tz,
            "budget": self.budget,
            "slots": list(self.slots),
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> Answers:
        raw = raw or {}
        slots = raw.get("slots") or []
        if not isinstance(slots, list):
            slots = []
        return cls(
            problem=raw.get("problem"),
            free_text=raw.get("free_text"),
            therapy_exp=raw.get("therapy_exp"),
            format=raw.get("format"),
            tz=raw.get("tz"),
            budget=raw.get("budget"),
            slots=[item for item in slots if item in SLOT_CODES],
        )

    def copy(self) -> Answers:
        return Answers.from_dict(self.to_dict())


@dataclass(frozen=True)
class Transition:
    screen: str
    answers: Answers
    alert: str | None = None
    submit: bool = False
    submit_mode: str | None = None


def needs_timezone(format_code: str | None) -> bool:
    return format_code in TZ_FORMATS


def missing_screen(answers: Answers) -> str | None:
    if answers.problem not in PROBLEM_CODES:
        return "S1"
    if answers.free_text is None:
        return "S2"
    if answers.therapy_exp not in EXPERIENCE_CODES:
        return "S3"
    if answers.format not in FORMAT_CODES:
        return "S4"
    if needs_timezone(answers.format):
        if answers.tz not in TIMEZONE_CODES:
            return "S4a"
    elif answers.tz != SKIPPED:
        return "S4"
    if answers.budget not in BUDGET_CODES:
        return "S5"
    if not answers.slots:
        return "S6"
    return None


def toggle_slot(slots: list[str], code: str) -> list[str]:
    chosen = set(slots)
    if code in chosen:
        chosen.remove(code)
    else:
        chosen.add(code)
    return [item for item in SLOT_ORDER if item in chosen]


def validate_free_text(text: str) -> tuple[str | None, str | None]:
    cleaned = text.strip()
    if not cleaned:
        return None, "Напишите короткий комментарий или нажмите «Пропустить»."
    if len(cleaned) > FREE_TEXT_LIMIT:
        return (
            None,
            f"Текст длиннее {FREE_TEXT_LIMIT} символов (сейчас {len(cleaned)}). "
            "Сократите его и отправьте ещё раз.",
        )
    return cleaned, None


def is_withdrawal(text: str | None) -> bool:
    if not text:
        return False
    cleaned = text.strip().casefold().strip("«»\"'“”")
    cleaned = cleaned.rstrip(".!…").strip()
    return cleaned == WITHDRAWAL_PHRASE


def one_line(text: str) -> str:
    return " ".join(text.split())


def comment_for_review(free_text: str | None) -> str:
    if free_text is None or free_text == SKIPPED or not free_text.strip():
        return "не указан"
    return one_line(free_text)


def slots_phrase(slots: list[str], *, card: bool) -> str:
    labels = [label_of(code, card=card) for code in slots]
    return ", ".join(labels) if labels else SKIPPED


def review_text(answers: Answers) -> str:
    tz = label_of(answers.tz) if answers.tz and answers.tz != SKIPPED else SKIPPED
    lines = [
        "Проверьте, всё ли верно.",
        "",
        f"Запрос: {label_of(answers.problem)}",
        f"Комментарий: {comment_for_review(answers.free_text)}",
        f"Опыт терапии: {label_of(answers.therapy_exp)}",
        f"Формат: {label_of(answers.format)}",
        f"Часовой пояс: {tz}",
        f"Бюджет: {label_of(answers.budget)}",
        f"Время: {slots_phrase(answers.slots, card=False)}",
    ]
    return "\n".join(lines)


def back_screen(screen: str, answers: Answers) -> str | None:
    mapping = {
        "S1": "S0",
        "S2": "S1",
        "S3": "S2",
        "S4": "S3",
        "S4a": "S4",
        "S6": "S5",
    }
    if screen in mapping:
        return mapping[screen]
    if screen == "S5":
        return "S4a" if needs_timezone(answers.format) else "S4"
    return None


def _select_format(answers: Answers, code: str) -> str:
    answers.format = code
    if code == "f_offline":
        answers.tz = SKIPPED
        return "S5"
    if answers.tz == SKIPPED:
        answers.tz = None
    return "S4a"


def apply(
    screen: str,
    action: str,
    answers: Answers,
    *,
    has_username: bool = True,
) -> Transition | None:
    current = answers.copy()

    if screen == "S0":
        if action == "agree":
            return Transition("S1", current)
        if action == "privacy":
            return Transition("P1", current)
        return None

    if screen == "P1":
        if action == "agree":
            return Transition("S1", current)
        if action == "back_s0":
            return Transition("S0", current)
        return None

    if screen == "S1":
        if action == "back":
            return Transition("S0", current)
        if action in PROBLEM_CODES:
            current.problem = action
            return Transition("S2", current)
        return None

    if screen == "S2":
        if action == "back":
            return Transition("S1", current)
        if action == "skip":
            current.free_text = SKIPPED
            return Transition("S3", current)
        if action == "keep" and current.free_text and current.free_text != SKIPPED:
            return Transition("S3", current)
        return None

    if screen == "S3":
        if action == "back":
            return Transition("S2", current)
        if action in EXPERIENCE_CODES:
            current.therapy_exp = action
            return Transition("S4", current)
        return None

    if screen == "S4":
        if action == "back":
            return Transition("S3", current)
        if action in FORMAT_CODES:
            destination = _select_format(current, action)
            return Transition(destination, current)
        return None

    if screen == "S4a":
        if action == "back":
            return Transition("S4", current)
        if action in TIMEZONE_CODES:
            current.tz = action
            return Transition("S5", current)
        return None

    if screen == "S5":
        if action == "back":
            return Transition(back_screen("S5", current) or "S4", current)
        if action in BUDGET_CODES:
            current.budget = action
            return Transition("S6", current)
        return None

    if screen == "S6":
        if action == "back":
            return Transition("S5", current)
        if action in SLOT_CODES:
            current.slots = toggle_slot(current.slots, action)
            return Transition("S6", current)
        if action == "t_done":
            if not current.slots:
                return Transition("S6", current, alert=EMPTY_DONE_ALERT)
            return Transition("S6b", current)
        return None

    if screen == "S6b":
        if action == "to_start":
            return Transition("S1", current)
        if action == "to_submit":
            gap = missing_screen(current)
            if gap:
                return Transition(gap, current)
            return Transition("S7" if has_username else "S7b", current)
        return None

    if screen == "S7" and action == "submit":
        if not has_username:
            return Transition("S7b", current)
        return Transition("S8", current, submit=True, submit_mode="username")

    if screen == "S7b":
        if action == "submit_nochat":
            return Transition("S8", current, submit=True, submit_mode="nochat")
        if action == "recheck_username":
            if has_username:
                return Transition("S8", current, submit=True, submit_mode="username")
            return Transition(
                "S7b",
                current,
                alert=(
                    "Никнейм пока не виден. Добавьте его в настройках Telegram "
                    "и нажмите кнопку ещё раз."
                ),
            )
        return None

    return None


def keyboard_rows(screen: str, answers: Answers) -> list[list[tuple[str, str]]]:
    def options(items: tuple[Option, ...], selected: str | None) -> list[list[tuple[str, str]]]:
        return [[(mark(item.label, item.code == selected), item.code)] for item in items]

    rows: list[list[tuple[str, str]]]
    if screen == "S0":
        rows = [
            [("Согласен(на), начать", "agree")],
            [("Как вы храните мои данные", "privacy")],
        ]
    elif screen == "P1":
        rows = [
            [("Согласен(на), начать", "agree")],
            [("Назад", "back_s0")],
        ]
    elif screen == "S1":
        rows = options(PROBLEMS, answers.problem)
    elif screen == "S2":
        rows = [[("Пропустить", "skip")]]
        if answers.free_text and answers.free_text != SKIPPED:
            rows.append([("Дальше", "keep")])
    elif screen == "S3":
        rows = options(EXPERIENCE, answers.therapy_exp)
    elif screen == "S4":
        rows = options(FORMATS, answers.format)
    elif screen == "S4a":
        rows = options(TIMEZONES, answers.tz if answers.tz != SKIPPED else None)
    elif screen == "S5":
        rows = options(BUDGETS, answers.budget)
    elif screen == "S6":
        chosen = set(answers.slots)
        rows = [[(mark(item.label, item.code in chosen), item.code)] for item in SLOTS]
        rows.append([("Готово", "t_done")])
    elif screen == "S6b":
        rows = [
            [("Всё верно, дальше", "to_submit")],
            [("Изменить ответы", "to_start")],
        ]
    elif screen == "S7":
        rows = [[("Отправить заявку куратору", "submit")]]
    elif screen == "S7b":
        rows = [
            [("Я добавил(а) никнейм", "recheck_username")],
            [("Отвечайте в этом чате", "submit_nochat")],
        ]
    else:
        rows = []

    if screen in BACK_SCREENS:
        rows.append([("Назад", "back")])
    return rows


@dataclass(frozen=True)
class CopySettings:
    operator_name: str = ""
    operator_inn: str = ""
    operator_email: str = ""
    response_time: str = "рабочего дня"
    privacy_url: str = ""


def operator_line(settings: CopySettings) -> str:
    name = settings.operator_name or "[ФИО]"
    passport = settings.operator_inn or "[номер]"
    email = settings.operator_email or "[почта]"
    return f"{name}, паспорт {passport}, e-mail: {email}"


def consent_text(settings: CopySettings) -> str:
    email = settings.operator_email or "[почта]"
    parts = [
        "СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ",
        "",
        f"Оператор: {operator_line(settings)}.",
        "",
        "Нажимая «Согласен(на), начать», вы даёте согласие "
        "на обработку ваших персональных данных.",
        "",
        "Какие данные: имя пользователя Telegram, идентификатор "
        "и имя аккаунта, ваши ответы на вопросы анкеты, текст "
        "сообщений в чате с ботом.",
        "",
        "Цель: подбор специалиста по вашему запросу и связь с вами "
        "для согласования консультации.",
        "",
        "Что делаем с данными: сбор, запись, хранение, уточнение, "
        "использование, передачу куратору сообщества и выбранному "
        "вами специалисту, удаление. Данные обрабатываются "
        "автоматизированно и без автоматического принятия решений.",
        "",
        "Срок: до отзыва согласия, но не более 1 года с даты "
        "заполнения анкеты. По истечении срока данные удаляются.",
        "",
        "Отзыв: напишите «Отзываю согласие» в этот чат "
        f"или на {email}. Данные будут удалены в течение 10 рабочих дней.",
        "",
        "Мы не передаём данные третьим лицам, кроме указанных выше, "
        "и не используем их для рекламы.",
        "",
        "Бот не оказывает психологическую и медицинскую помощь, "
        "не ставит диагнозы и не предназначен для ситуаций, "
        "требующих срочной помощи.",
    ]
    if settings.privacy_url:
        parts.extend(["", f"Постоянная ссылка на этот текст: {settings.privacy_url}"])
    return "\n".join(parts)


def screen_text(screen: str, answers: Answers, settings: CopySettings) -> str:
    if screen == "S0":
        return (
            "Здравствуйте! Это бот сообщества «Практика».\n"
            "\n"
            "Мы собираем анкету, чтобы куратор мог предложить вам "
            "специалиста под ваш запрос.\n"
            "\n"
            "Бот не оказывает психологическую помощь, не ставит диагнозы "
            "и не предназначен для ситуаций, требующих срочной помощи.\n"
            "\n"
            "Ответьте на 6 коротких вопросов — это займёт около 3 минут. "
            "Ваши ответы видят только куратор сообщества и специалист, "
            "которого мы предложим."
        )
    if screen == "P1":
        return consent_text(settings)
    if screen == "S1":
        return (
            "Вопрос 1 из 6\n"
            "\n"
            "С чем вы хотите поработать?\n"
            "Выберите то, что беспокоит сильнее всего — остальное "
            "можно будет обсудить со специалистом."
        )
    if screen == "S2":
        base = (
            "Вопрос 2 из 6\n"
            "\n"
            "Опишите в двух-трёх предложениях, что происходит.\n"
            "Подробности не нужны — достаточно общей рамки.\n"
            "\n"
            "Если не хотите уточнять — нажмите «Пропустить»."
        )
        if answers.free_text and answers.free_text != SKIPPED:
            base += (
                f"\n\nСейчас в анкете: {one_line(answers.free_text)}\n"
                "Отправьте новый текст, чтобы заменить его, или нажмите «Дальше»."
            )
        return base
    if screen == "S3":
        return (
            "Вопрос 3 из 6\n"
            "\n"
            "Был ли у вас опыт работы с психологом или психотерапевтом?\n"
            "\n"
            "Это помогает не предлагать то, что вам уже не подошло."
        )
    if screen == "S4":
        return (
            "Вопрос 4 из 6\n"
            "\n"
            "В каком формате вам удобнее работать?\n"
            "\n"
            "Онлайн-сессии по эффективности сопоставимы с очными, "
            "так что выбирайте то, что проще встроить в расписание."
        )
    if screen == "S4a":
        return (
            "В каком часовом поясе вы находитесь?\n"
            "Это нужно, чтобы сразу предложить реальные слоты."
        )
    if screen == "S5":
        return (
            "Вопрос 5 из 6\n"
            "\n"
            "Какая стоимость одной сессии будет для вас комфортной?\n"
            "\n"
            "Терапия — это обычно не одна встреча, поэтому лучше назвать "
            "сумму, которую вы сможете платить регулярно, "
            "а не максимум на разовый приём."
        )
    if screen == "S6":
        text = (
            "Вопрос 6 из 6\n"
            "\n"
            "Когда вам удобнее встречи? Отметьте все подходящие варианты — "
            "чем больше окон, тем быстрее найдём специалиста "
            "со свободным местом."
        )
        if answers.slots:
            text += f"\n\nВыбрано: {slots_phrase(answers.slots, card=False)}"
        return text
    if screen == "S6b":
        return review_text(answers)
    if screen == "S7":
        return (
            "Анкета готова.\n"
            "\n"
            "Нажмите кнопку ниже — мы передадим её куратору вместе "
            "с вашим ником в Telegram. Куратор напишет вам в личные сообщения."
        )
    if screen == "S7b":
        return (
            "Почти готово.\n"
            "\n"
            "У вашего аккаунта не указано имя пользователя (@никнейм), "
            "поэтому куратор не сможет написать вам первым.\n"
            "\n"
            "Есть два варианта: добавить никнейм в настройках Telegram "
            "(Настройки → Имя пользователя) и нажать «Я добавил(а) никнейм», "
            "либо оставить всё как есть — тогда мы ответим вам "
            "прямо в этом чате."
        )
    if screen == "S8":
        term = settings.response_time or "рабочего дня"
        return (
            "Готово, анкета у куратора.\n"
            "\n"
            f"В течение {term} мы напишем вам в Telegram и предложим "
            "1–2 специалистов с описанием опыта и стоимости.\n"
            "\n"
            "Выбор остаётся за вами: если после первой встречи не возникнет "
            "контакта со специалистом — скажите нам, подберём другого. "
            "Это нормальная часть процесса.\n"
            "\n"
            "Если захотите что-то добавить или уточнить — просто "
            "напишите в этот чат."
        )
    return ""


def person_name(first_name: str | None, last_name: str | None) -> str:
    parts = [part.strip() for part in (first_name or "", last_name or "") if part and part.strip()]
    return " ".join(parts) if parts else SKIPPED


def utc_now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_local(value: str, tz_name: str) -> str:
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        zone = ZoneInfo("Europe/Moscow")
    return parse_utc(value).astimezone(zone).strftime("%d.%m.%Y, %H:%M")


def card_lines(
    *,
    created_at: str,
    tz_name: str,
    username: str,
    full_name: str,
    user_id: int,
    answers: Answers,
    number: int | None = None,
    reply_in_chat: bool = False,
) -> list[str]:
    header = f"НОВАЯ ЗАЯВКА · {format_local(created_at, tz_name)}"

    has_nick = bool(username) and username != NO_USERNAME and not reply_in_chat
    if has_nick:
        client = f"@{username}"
        if full_name and full_name != SKIPPED:
            client += f" ({full_name})"
    else:
        client = f"без никнейма, отвечать через бота, ID: {user_id}"

    tz_short = ""
    if answers.tz and answers.tz != SKIPPED:
        tz_short = label_of(answers.tz, card=True)
        client += f" · TZ: {tz_short}"

    lines = [
        header,
        "",
        f"КЛИЕНТ: {client}",
        f"ЗАПРОС: {label_of(answers.problem, card=True)}",
    ]
    if answers.free_text and answers.free_text != SKIPPED:
        lines.append(f"КОММЕНТАРИЙ: {one_line(answers.free_text)}")
    lines.extend(
        [
            f"ОПЫТ: {label_of(answers.therapy_exp, card=True)}",
            (
                f"ФОРМАТ: {label_of(answers.format, card=True)} · "
                f"{label_of(answers.budget, card=True)}"
            ),
            f"ВРЕМЯ: {slots_phrase(answers.slots, card=True)}",
        ]
    )
    if number is not None:
        lines.append(f"НОМЕР: {number}")
    return lines


def card_text(**kwargs) -> str:
    return "\n".join(card_lines(**kwargs))


def supplement_text(
    *,
    application_id: int,
    username: str,
    full_name: str,
    user_id: int,
    body: str,
) -> str:
    if username and username != NO_USERNAME:
        who = f"@{username}"
        if full_name and full_name != SKIPPED:
            who += f" ({full_name})"
    else:
        who = "без никнейма"
    text = one_line(body) if body else "[сообщение без текста]"
    return (
        f"Дополнение к заявке #{application_id}\n"
        f"От: {who} · ID: {user_id}\n"
        f"\n"
        f"{text}"
    )


def withdrawal_reply(*, removed: int) -> str:
    if removed:
        return (
            "Согласие отозвано. Заявка и связанные данные удалены из бота.\n"
            "\n"
            "Если захотите заполнить анкету снова — отправьте /start."
        )
    return (
        "Согласие отозвано. Сохранённых заявок не было, текущие ответы удалены.\n"
        "\n"
        "Если захотите заполнить анкету снова — отправьте /start."
    )


@dataclass(frozen=True)
class FunnelRow:
    screen: str
    title: str
    opened: int
    left: int

    @property
    def left_percent(self) -> float:
        if not self.opened or self.screen == "S8":
            return 0.0
        return round(self.left * 100 / self.opened, 1)


def funnel_stats(sessions: list[set[str]]) -> list[FunnelRow]:
    rows: list[FunnelRow] = []
    for screen in FUNNEL_SCREENS:
        opened = sum(1 for screens in sessions if screen in screens)
        nxt = NEXT_SCREENS[screen]
        if not nxt:
            left = 0
        else:
            left = sum(1 for screens in sessions if screen in screens and not (screens & nxt))
        rows.append(
            FunnelRow(
                screen=screen,
                title=SCREEN_TITLES[screen],
                opened=opened,
                left=left,
            )
        )
    return rows


def format_funnel(rows: list[FunnelRow]) -> str:
    lines = ["Отвал по экранам", ""]
    if not any(row.opened for row in rows):
        lines.append("Пока нет ни одной сессии.")
        return "\n".join(lines)
    for row in rows:
        if row.screen == "S8":
            lines.append(f"{row.title} — {row.opened}")
            continue
        lines.append(f"{row.title} — {row.opened}, ушли {row.left} ({row.left_percent}%)")
    lines.extend(
        [
            "",
            "Процент считается от тех, кто открыл экран и не дошёл до следующего шага.",
            "S4a показывается только для «Онлайн» и «Не принципиально».",
        ]
    )
    return "\n".join(lines)


def decode_status(code: str) -> str | None:
    return {"n": "new", "w": "in_progress", "c": "closed"}.get(code)


def decode_period(code: str) -> str | None:
    return {"d": "today", "w": "7d", "m": "30d"}.get(code)


def decode_topic(code: str) -> str | None:
    if code in PROBLEM_CODES:
        return code
    return None


def period_start(period: str | None, now: datetime, tz_name: str) -> datetime | None:
    if not period:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        zone = ZoneInfo("Europe/Moscow")
    current = now.astimezone(timezone.utc)
    if period == "today":
        local = current.astimezone(zone)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(timezone.utc)
    if period == "7d":
        return current - timedelta(days=7)
    if period == "30d":
        return current - timedelta(days=30)
    return None


_FORMULA = re.compile(r"^[=+\-@\t\r]")


def csv_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text and _FORMULA.match(text):
        return "'" + text
    return text


def applications_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "id",
            "created_at",
            "status",
            "tg_user_id",
            "tg_username",
            "tg_name",
            "consent_at",
            "problem",
            "free_text",
            "therapy_exp",
            "format",
            "tz",
            "budget",
            "slots",
            "reply_in_chat",
        ]
    )
    for row in rows:
        writer.writerow([csv_cell(row.get(key)) for key in _CSV_FIELDS])
    return buffer.getvalue()


_CSV_FIELDS = (
    "id",
    "created_at",
    "status",
    "tg_user_id",
    "tg_username",
    "tg_name",
    "consent_at",
    "problem",
    "free_text",
    "therapy_exp",
    "format",
    "tz",
    "budget",
    "slots",
    "reply_in_chat",
)


def can_view(role: str | None) -> bool:
    return role in {"admin", "operator"}


def can_export(role: str | None) -> bool:
    return role == "admin"


def can_delete(role: str | None) -> bool:
    return role == "admin"
