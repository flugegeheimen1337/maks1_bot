from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Config
from bot.db import Application, Database, Page, Supplement
from bot.domain import (
    PERIOD_FILTERS,
    PROBLEMS,
    STATUSES,
    STATUS_FILTERS,
    can_delete,
    can_export,
    decode_period,
    decode_status,
    decode_topic,
    format_funnel,
    format_local,
    funnel_stats,
    label_of,
    one_line,
    applications_csv,
    card_lines,
)
from bot.services import delete_messages, erase_user

router = Router(name="admin")

HELP = (
    "Заявки анкеты.\n"
    "\n"
    "/apps — список\n"
    "/app 12 — одна заявка\n"
    "/find ник или id — поиск\n"
    "/stats — отвал по экранам\n"
    "/export — CSV, только у администратора\n"
    "/delete 12 — удалить заявку\n"
    "/delete user 123456 — удалить все данные человека\n"
    "\n"
    "Оператор может смотреть заявки и менять статус. "
    "Удаление и выгрузка — только у администратора."
)


class Staff(Filter):
    def __init__(self, include_operators: bool = True) -> None:
        self.include_operators = include_operators

    async def __call__(
        self,
        event: Message | CallbackQuery,
        staff_role: str | None = None,
    ) -> bool:
        message = event if isinstance(event, Message) else event.message
        if not isinstance(message, Message) or message.chat.type != "private":
            return False
        if staff_role == "admin":
            return True
        return bool(self.include_operators and staff_role == "operator")


def _mark(title: str, active: bool) -> str:
    return f"✅ {title}" if active else title


def _topic_title(code: str) -> str:
    if code == "a":
        return "все темы"
    return label_of(code)


def list_caption(page: Page, status: str, topic: str, period: str, tz_name: str) -> str:
    status_label = dict(STATUS_FILTERS).get(status, "Все")
    period_label = dict(PERIOD_FILTERS).get(period, "Все даты")
    lines = [
        "Заявки",
        f"Фильтр: {status_label.lower()} · {_topic_title(topic)} · {period_label.lower()}",
        f"Найдено: {page.total}",
        "",
    ]
    if not page.items:
        lines.append("Ничего не нашлось.")
        return "\n".join(lines)
    for item in page.items:
        who = f"@{item.tg_username}" if item.tg_username != "нет никнейма" else f"id {item.tg_user_id}"
        lines.append(
            f"#{item.id} · {format_local(item.created_at, tz_name)} · {STATUSES.get(item.status, item.status)}\n"
            f"{who} · {label_of(item.problem)}"
        )
    lines.append("")
    lines.append(f"Страница {page.page + 1} из {page.pages}")
    return "\n".join(lines)


def list_keyboard(
    page: Page,
    status: str,
    topic: str,
    period: str,
    *,
    include_export: bool,
    search: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in page.items:
        if search:
            callback = f"vw:{item.id}:s:{page.page}"
        else:
            callback = f"vw:{item.id}:l:{status}:{topic}:{period}:{page.page}"
        rows.append([InlineKeyboardButton(text=f"Открыть #{item.id}", callback_data=callback)])

    if not search:
        nav: list[InlineKeyboardButton] = []
        if page.page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="←",
                    callback_data=f"ls:{status}:{topic}:{period}:{page.page - 1}",
                )
            )
        if page.page + 1 < page.pages:
            nav.append(
                InlineKeyboardButton(
                    text="→",
                    callback_data=f"ls:{status}:{topic}:{period}:{page.page + 1}",
                )
            )
        if nav:
            rows.append(nav)
        rows.append(
            [
                InlineKeyboardButton(
                    text=_mark(title, code == status),
                    callback_data=f"ls:{code}:{topic}:{period}:0",
                )
                for code, title in STATUS_FILTERS
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=_mark(title, code == period),
                    callback_data=f"ls:{status}:{topic}:{code}:0",
                )
                for code, title in PERIOD_FILTERS
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Тема: {_topic_title(topic)}",
                    callback_data=f"ft:{status}:{topic}:{period}",
                )
            ]
        )
        if include_export:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Выгрузка CSV",
                        callback_data=f"ex:{status}:{topic}:{period}",
                    )
                ]
            )
    else:
        nav = []
        if page.page > 0:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"fq:{page.page - 1}"))
        if page.page + 1 < page.pages:
            nav.append(InlineKeyboardButton(text="→", callback_data=f"fq:{page.page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="К списку", callback_data="ls:a:a:a:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_keyboard(status: str, period: str, topic: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Все темы", callback_data=f"ls:{status}:a:{period}:0")]]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=item.label,
                    callback_data=f"ls:{status}:{item.code}:{period}:0",
                )
            ]
            for item in PROBLEMS
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="Назад", callback_data=f"ls:{status}:{topic}:{period}:0")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_text(
    application: Application,
    supplements: list[Supplement],
    tz_name: str,
) -> str:
    lines = card_lines(
        created_at=application.created_at,
        tz_name=tz_name,
        username=application.tg_username,
        full_name=application.tg_name,
        user_id=application.tg_user_id,
        answers=application.answers(),
        number=application.id,
        reply_in_chat=application.reply_in_chat,
    )
    lines[0] = (
        f"Заявка #{application.id} · {STATUSES.get(application.status, application.status)} · "
        f"{format_local(application.created_at, tz_name)}"
    )
    if supplements:
        lines.append("")
        lines.append("Дополнения:")
        for item in supplements[-5:]:
            lines.append(f"— {one_line(item.text)[:300]}")
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3999] + "…"


def application_keyboard(
    application: Application,
    *,
    include_delete: bool,
    back: str,
) -> InlineKeyboardMarkup:
    origin = _origin_tail(back)
    rows = [
        [
            InlineKeyboardButton(text=_mark("Новая", application.status == "new"), callback_data=f"st:{application.id}:n:{origin}"),
            InlineKeyboardButton(text=_mark("В работе", application.status == "in_progress"), callback_data=f"st:{application.id}:w:{origin}"),
            InlineKeyboardButton(text=_mark("Закрыта", application.status == "closed"), callback_data=f"st:{application.id}:c:{origin}"),
        ],
        [InlineKeyboardButton(text="К списку", callback_data=back)],
    ]
    if include_delete:
        rows.append([InlineKeyboardButton(text="Удалить", callback_data=f"dl:{application.id}:ask")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _origin_tail(back: str) -> str:
    # ls:n:c_anx:d:0 -> l:n:c_anx:d:0 ; fq:1 -> s:1
    parts = back.split(":")
    if parts[0] == "fq":
        return f"s:{parts[1]}"
    if parts[0] == "ls" and len(parts) == 5:
        return f"l:{parts[1]}:{parts[2]}:{parts[3]}:{parts[4]}"
    return "l:a:a:a:0"


def _back_from_origin(origin: list[str]) -> str:
    if origin and origin[0] == "s":
        page = origin[1] if len(origin) > 1 else "0"
        return f"fq:{page}"
    if origin and origin[0] == "l" and len(origin) >= 5:
        return f"ls:{origin[1]}:{origin[2]}:{origin[3]}:{origin[4]}"
    return "ls:a:a:a:0"


async def _show_list(
    target: Message,
    db: Database,
    config: Config,
    *,
    status: str,
    topic: str,
    period: str,
    page: int,
    staff_role: str | None,
    edit: bool,
) -> None:
    result = await db.list_applications(
        status=decode_status(status),
        topic=decode_topic(topic),
        period=decode_period(period),
        page=page,
        tz_name=config.curator_tz,
    )
    text = list_caption(result, status, topic, period, config.curator_tz)
    markup = list_keyboard(
        result,
        status,
        topic,
        period,
        include_export=can_export(staff_role),
    )
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except TelegramAPIError as exc:
            if "message is not modified" in str(exc).lower():
                return
    await target.answer(text, reply_markup=markup)


async def _show_application(
    target: Message,
    db: Database,
    config: Config,
    application_id: int,
    *,
    staff_role: str | None,
    back: str,
    edit: bool,
) -> None:
    application = await db.get_application(application_id)
    if application is None:
        if edit:
            await target.edit_text("Заявка уже удалена.")
        else:
            await target.answer("Заявка не найдена.")
        return
    supplements = await db.list_supplements(application.id)
    text = application_text(application, supplements, config.curator_tz)
    markup = application_keyboard(
        application,
        include_delete=can_delete(staff_role),
        back=back,
    )
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except TelegramAPIError as exc:
            if "message is not modified" in str(exc).lower():
                return
    await target.answer(text, reply_markup=markup)


async def _send_export(
    message: Message,
    db: Database,
    config: Config,
    *,
    status: str,
    topic: str,
    period: str,
) -> None:
    rows = await db.iter_applications(
        status=decode_status(status),
        topic=decode_topic(topic),
        period=decode_period(period),
        tz_name=config.curator_tz,
    )
    payload = applications_csv([row.csv_row() for row in rows])
    filename = "zayavki.csv"
    await message.answer_document(
        BufferedInputFile(payload.encode("utf-8-sig"), filename=filename),
        caption=f"Заявок в файле: {len(rows)}",
    )
    actor_id = message.from_user.id if message.from_user else message.chat.id
    await db.audit(
        actor=f"admin:{actor_id}",
        action="export",
        details={"rows": len(rows), "status": status, "topic": topic, "period": period},
    )


@router.message(Command("admin"), Staff())
async def cmd_admin(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("apps"), Staff())
async def cmd_apps(message: Message, db: Database, config: Config, staff_role: str | None = None) -> None:
    await _show_list(
        message,
        db,
        config,
        status="a",
        topic="a",
        period="a",
        page=0,
        staff_role=staff_role,
        edit=False,
    )


@router.message(Command("stats"), Staff())
async def cmd_stats(message: Message, db: Database) -> None:
    rows = funnel_stats(await db.funnel_sessions())
    await message.answer(format_funnel(rows))


@router.message(Command("app"), Staff())
async def cmd_app(
    message: Message,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer("Напишите номер: /app 12")
        return
    await _show_application(
        message,
        db,
        config,
        int(parts[1].strip()),
        staff_role=staff_role,
        back="ls:a:a:a:0",
        edit=False,
    )


@router.message(Command("find"), Staff())
async def cmd_find(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Напишите ник или id: /find username")
        return
    query = parts[1].strip()
    await state.update_data(admin_query=query)
    await _show_search(message, db, config, query=query, page=0, staff_role=staff_role, edit=False)


async def _show_search(
    target: Message,
    db: Database,
    config: Config,
    *,
    query: str,
    page: int,
    staff_role: str | None,
    edit: bool,
) -> None:
    result = await db.search(query, page=page)
    who = f"Поиск: {query}"
    text = f"{who}\nНайдено: {result.total}\n\n" + "\n".join(
        f"#{item.id} · {label_of(item.problem)} · {STATUSES.get(item.status, item.status)}"
        for item in result.items
    )
    if not result.items:
        text += "Ничего не нашлось."
    markup = list_keyboard(result, "a", "a", "a", include_export=False, search=True)
    if edit:
        try:
            await target.edit_text(text, reply_markup=markup)
            return
        except TelegramAPIError as exc:
            if "message is not modified" in str(exc).lower():
                return
    await target.answer(text, reply_markup=markup)


@router.message(Command("export"), Staff(include_operators=False))
async def cmd_export(message: Message, db: Database, config: Config) -> None:
    await _send_export(message, db, config, status="a", topic="a", period="a")


@router.message(Command("delete"), Staff(include_operators=False))
async def cmd_delete(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) == 3 and parts[1].lower() == "user" and parts[2].isdigit():
        user_id = parts[2]
        await message.answer(
            f"Удалить все данные пользователя {user_id}? Заявки и ответы будут стёрты.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Удалить данные", callback_data=f"wu:{user_id}:yes"),
                        InlineKeyboardButton(text="Отмена", callback_data=f"wu:{user_id}:no"),
                    ]
                ]
            ),
        )
        return
    if len(parts) == 2 and parts[1].isdigit():
        app_id = parts[1]
        await message.answer(
            f"Удалить заявку #{app_id}?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Удалить", callback_data=f"dl:{app_id}:yes"),
                        InlineKeyboardButton(text="Отмена", callback_data="ls:a:a:a:0"),
                    ]
                ]
            ),
        )
        return
    await message.answer("Формат: /delete 12 или /delete user 123456")


@router.message(Command("export", "delete"), Staff())
async def cmd_admin_only(message: Message) -> None:
    await message.answer("Эта команда доступна только администратору.")


@router.callback_query(F.data.regexp(r"^ls:"), Staff())
async def cb_list(
    callback: CallbackQuery,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("Некорректный фильтр", show_alert=True)
        return
    _, status, topic, period, page_raw = parts
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    await callback.answer()
    await _show_list(
        message,
        db,
        config,
        status=status,
        topic=topic,
        period=period,
        page=page,
        staff_role=staff_role,
        edit=True,
    )


@router.callback_query(F.data.regexp(r"^ft:"), Staff())
async def cb_topics(callback: CallbackQuery) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    _, status, topic, period = parts
    await callback.answer()
    await message.edit_text(
        "Выберите тему запроса.",
        reply_markup=topic_keyboard(status, period, topic),
    )


@router.callback_query(F.data.regexp(r"^vw:"), Staff())
async def cb_view(
    callback: CallbackQuery,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        application_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    origin = parts[2:]
    await callback.answer()
    await _show_application(
        message,
        db,
        config,
        application_id,
        staff_role=staff_role,
        back=_back_from_origin(origin),
        edit=True,
    )


@router.callback_query(F.data.regexp(r"^st:"), Staff())
async def cb_status(
    callback: CallbackQuery,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    message = callback.message
    if not isinstance(message, Message) or callback.from_user is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        application_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    status = decode_status(parts[2]) if len(parts) > 2 else None
    if status is None:
        await callback.answer("Неизвестный статус", show_alert=True)
        return
    updated = await db.set_status(application_id, status)
    if updated is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await db.audit(
        actor=f"staff:{callback.from_user.id}",
        action="set_status",
        target=str(application_id),
        details={"status": status},
    )
    await callback.answer("Статус обновлён")
    await _show_application(
        message,
        db,
        config,
        application_id,
        staff_role=staff_role,
        back=_back_from_origin(parts[3:]),
        edit=True,
    )


@router.callback_query(F.data.regexp(r"^fq:"), Staff())
async def cb_search_page(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    config: Config,
    staff_role: str | None = None,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    data = await state.get_data()
    query = data.get("admin_query")
    if not query:
        await callback.answer("Повторите /find", show_alert=True)
        return
    try:
        page = int((callback.data or "").split(":")[1])
    except (IndexError, ValueError):
        page = 0
    await callback.answer()
    await _show_search(
        message,
        db,
        config,
        query=str(query),
        page=page,
        staff_role=staff_role,
        edit=True,
    )


@router.callback_query(F.data.regexp(r"^ex:"), Staff(include_operators=False))
async def cb_export(callback: CallbackQuery, db: Database, config: Config) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    _, status, topic, period = parts
    await callback.answer("Готовлю файл")
    await _send_export(message, db, config, status=status, topic=topic, period=period)


@router.callback_query(F.data.regexp(r"^dl:"), Staff(include_operators=False))
async def cb_delete(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    message = callback.message
    if not isinstance(message, Message) or callback.from_user is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        application_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    action = parts[2] if len(parts) > 2 else ""
    if action == "ask":
        await callback.answer()
        await message.edit_text(
            f"Удалить заявку #{application_id}? Ответы будут стёрты из базы.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Удалить", callback_data=f"dl:{application_id}:yes"),
                        InlineKeyboardButton(text="Отмена", callback_data="ls:a:a:a:0"),
                    ]
                ]
            ),
        )
        return
    if action != "yes":
        await callback.answer()
        return
    application = await db.get_application(application_id)
    if application is None:
        await callback.answer("Заявка уже удалена", show_alert=True)
        return
    supplements = await db.list_supplements(application_id)
    message_ids = [item for item in [application.curator_message_id] if item]
    for supplement in supplements:
        message_ids.extend(supplement.curator_message_ids)
    await db.delete_application(application_id)
    await db.audit(
        actor=f"admin:{callback.from_user.id}",
        action="delete_application",
        target=str(application_id),
        details={"tg_user_id": application.tg_user_id},
    )
    await delete_messages(bot, config.applications_chat_id, message_ids)
    await callback.answer("Удалено")
    await message.edit_text(f"Заявка #{application_id} удалена.")


@router.callback_query(F.data.regexp(r"^wu:"), Staff(include_operators=False))
async def cb_wipe(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    message = callback.message
    if not isinstance(message, Message) or callback.from_user is None:
        await callback.answer()
        return
    parts = (callback.data or "").split(":")
    try:
        user_id = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer()
        return
    action = parts[2] if len(parts) > 2 else ""
    if action == "no":
        await callback.answer("Отменено")
        await message.edit_text("Удаление отменено.")
        return
    if action != "yes":
        await callback.answer()
        return
    removed = await erase_user(
        bot=bot,
        db=db,
        config=config,
        state=state,
        user_id=user_id,
        actor=f"admin:{callback.from_user.id}",
    )
    await callback.answer("Данные удалены")
    await message.edit_text(
        f"Данные пользователя {user_id} удалены. Заявок было: {removed}."
    )


@router.callback_query(F.data.regexp(r"^(ls|vw|st|dl|ft|ex|fq|wu):"))
async def cb_denied(callback: CallbackQuery) -> None:
    await callback.answer("Недостаточно прав.", show_alert=True)
