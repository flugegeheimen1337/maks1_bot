from datetime import datetime, timezone

from bot.domain import (
    FREE_TEXT_LIMIT,
    SKIPPED,
    Answers,
    CopySettings,
    apply,
    applications_csv,
    can_delete,
    can_export,
    can_view,
    card_text,
    comment_for_review,
    consent_text,
    funnel_stats,
    is_withdrawal,
    keyboard_rows,
    label_of,
    missing_screen,
    period_start,
    review_text,
    screen_text,
    validate_free_text,
)


def _filled() -> Answers:
    return Answers(
        problem="c_anx",
        free_text="Трясёт перед выступлениями, стало мешать работе",
        therapy_exp="e_fail",
        format="f_online",
        tz="tz3",
        budget="p3",
        slots=["t_eve", "t_week"],
    )


def test_online_path_reaches_submit():
    answers = Answers()
    step = apply("S0", "agree", answers)
    assert step is not None and step.screen == "S1"
    step = apply("S1", "c_anx", step.answers)
    assert step.screen == "S2"
    step = apply("S2", "skip", step.answers)
    assert step.screen == "S3" and step.answers.free_text == SKIPPED
    step = apply("S3", "e_fail", step.answers)
    assert step.screen == "S4"
    step = apply("S4", "f_online", step.answers)
    assert step.screen == "S4a"
    step = apply("S4a", "tz3", step.answers)
    assert step.screen == "S5" and step.answers.tz == "tz3"
    step = apply("S5", "p3", step.answers)
    assert step.screen == "S6"
    step = apply("S6", "t_week", step.answers)
    step = apply("S6", "t_eve", step.answers)
    assert step.answers.slots == ["t_eve", "t_week"]
    empty = apply("S6", "t_done", Answers())
    assert empty is not None and empty.alert
    step = apply("S6", "t_done", step.answers)
    assert step.screen == "S6b" and step.alert is None
    step = apply("S6b", "to_submit", step.answers, has_username=True)
    assert step.screen == "S7"
    step = apply("S7", "submit", step.answers, has_username=True)
    assert step.submit and step.submit_mode == "username" and step.screen == "S8"


def test_office_skips_timezone_and_back_returns_to_format():
    answers = Answers(problem="c_rel", free_text=SKIPPED, therapy_exp="e_first")
    step = apply("S4", "f_offline", answers)
    assert step is not None
    assert step.screen == "S5"
    assert step.answers.tz == SKIPPED
    back = apply("S5", "back", step.answers)
    assert back is not None and back.screen == "S4"
    again = apply("S4", "f_online", back.answers)
    assert again is not None
    assert again.screen == "S4a"
    assert again.answers.tz is None


def test_username_branch_and_edit_keeps_answers():
    answers = _filled()
    no_nick = apply("S6b", "to_submit", answers, has_username=False)
    assert no_nick is not None and no_nick.screen == "S7b"
    retry = apply("S7b", "recheck_username", no_nick.answers, has_username=False)
    assert retry is not None and retry.screen == "S7b" and retry.alert
    ready = apply("S7b", "recheck_username", no_nick.answers, has_username=True)
    assert ready is not None and ready.submit and ready.submit_mode == "username"
    chat = apply("S7b", "submit_nochat", no_nick.answers, has_username=False)
    assert chat is not None and chat.submit_mode == "nochat"
    edited = apply("S6b", "to_start", answers)
    assert edited is not None and edited.screen == "S1"
    assert edited.answers.problem == "c_anx"
    assert edited.answers.slots == ["t_eve", "t_week"]


def test_lost_username_on_submit_goes_to_fallback():
    step = apply("S7", "submit", _filled(), has_username=False)
    assert step is not None and step.screen == "S7b" and not step.submit


def test_wrong_button_is_ignored():
    assert apply("S1", "submit", Answers()) is None
    assert apply("S0", "back", Answers()) is None


def test_free_text_limit():
    ok, error = validate_free_text("  " + "а" * FREE_TEXT_LIMIT + "  ")
    assert error is None and ok is not None and len(ok) == FREE_TEXT_LIMIT
    text, error = validate_free_text("б" * (FREE_TEXT_LIMIT + 1))
    assert text is None and error and "500" in error
    text, error = validate_free_text("   ")
    assert text is None and error


def test_withdrawal_phrase():
    assert is_withdrawal("Отзываю согласие")
    assert is_withdrawal("  отзываю согласие. ")
    assert is_withdrawal("«Отзываю согласие»")
    assert not is_withdrawal("Отзываю согласие, пожалуйста удалите")
    assert not is_withdrawal("/start")


def test_review_and_curator_card():
    answers = _filled()
    review = review_text(answers)
    assert "Запрос: Тревога и панические атаки" in review
    assert "Комментарий: Трясёт перед выступлениями, стало мешать работе" in review
    assert "Часовой пояс: UTC+3 (Москва)" in review
    assert "Время: Будни после 18:00, Выходные" in review

    skipped = _filled()
    skipped.free_text = SKIPPED
    skipped.tz = SKIPPED
    skipped.format = "f_offline"
    assert comment_for_review(skipped.free_text) == "не указан"
    skipped_review = review_text(skipped)
    assert "Комментарий: не указан" in skipped_review
    assert "Часовой пояс: —" in skipped_review

    card = card_text(
        created_at="2026-11-18T17:14:00Z",
        tz_name="Europe/Moscow",
        username="username",
        full_name="Имя Фамилия",
        user_id=123456789,
        answers=answers,
        number=7,
    )
    assert card.startswith("НОВАЯ ЗАЯВКА · 18.11.2026, 20:14\n")
    assert "КЛИЕНТ: @username (Имя Фамилия) · TZ: UTC+3" in card
    assert "ЗАПРОС: Тревога и панические атаки" in card
    assert "КОММЕНТАРИЙ: Трясёт перед выступлениями, стало мешать работе" in card
    assert "ОПЫТ: Был, не помогло" in card
    assert "ФОРМАТ: онлайн · 5 000–8 000 ₽" in card
    assert "ВРЕМЯ: будни после 18:00, выходные" in card
    assert card.endswith("НОМЕР: 7")

    hidden = card_text(
        created_at="2026-11-18T17:14:00Z",
        tz_name="Europe/Moscow",
        username="username",
        full_name="Имя",
        user_id=123456789,
        answers=skipped,
        reply_in_chat=True,
    )
    assert "КЛИЕНТ: без никнейма, отвечать через бота, ID: 123456789" in hidden
    assert "TZ:" not in hidden
    assert "КОММЕНТАРИЙ:" not in hidden

    multiline = _filled()
    multiline.free_text = "первая строка\nвторая   строка"
    collapsed = card_text(
        created_at="2026-11-18T17:14:00Z",
        tz_name="Europe/Moscow",
        username="username",
        full_name="Имя",
        user_id=1,
        answers=multiline,
    )
    assert "КОММЕНТАРИЙ: первая строка вторая строка" in collapsed


def test_screens_and_keyboards():
    settings = CopySettings(response_time="24 часов", privacy_url="https://example.com/privacy")
    welcome = screen_text("S0", Answers(), settings)
    assert "не оказывает психологическую помощь" in welcome
    assert "8-800" not in welcome
    assert "112" not in welcome
    consent = consent_text(settings)
    assert "Отзываю согласие" in consent
    assert "здоровье" not in consent.casefold()
    assert "https://example.com/privacy" in consent
    assert "[ФИО]" in consent
    assert "паспорт [номер]" in consent
    filled = consent_text(
        CopySettings(
            operator_name="Иванов Иван Иванович",
            operator_inn="0000 000000",
            operator_email="mail@example.com",
        )
    )
    assert "Оператор: Иванов Иван Иванович, паспорт 0000 000000, e-mail: mail@example.com." in filled
    assert "ИНН" not in filled
    final = screen_text("S8", Answers(), settings)
    assert "В течение 24 часов" in final

    for screen in ("S1", "S2", "S3", "S4", "S4a", "S5", "S6"):
        rows = keyboard_rows(screen, _filled())
        assert rows[-1] == [("Назад", "back")]
        assert all(len(row) == 1 for row in rows)

    slots = keyboard_rows("S6", _filled())
    assert ("✅ Будни после 18:00", "t_eve") in [row[0] for row in slots]
    assert ("Готово", "t_done") in [row[0] for row in slots]
    assert ("Дальше", "keep") in [row[0] for row in keyboard_rows("S2", _filled())]
    assert ("Дальше", "keep") not in [row[0] for row in keyboard_rows("S2", Answers())]
    assert missing_screen(_filled()) is None
    assert missing_screen(Answers()) == "S1"
    assert label_of("f_online", card=True) == "онлайн"


def test_funnel_dropoff():
    sessions = [
        {"S0", "P1"},
        {"S0", "S1", "S2", "S3", "S4", "S5"},
        {"S0", "S1", "S2", "S3", "S4", "S4a", "S5", "S6", "S6b", "S7", "S8"},
    ]
    rows = {row.screen: row for row in funnel_stats(sessions)}
    assert rows["S0"].opened == 3
    assert rows["S0"].left == 1
    assert rows["S4"].opened == 2
    assert rows["S4"].left == 0
    assert rows["S4a"].opened == 1
    assert rows["S4a"].left == 0
    assert rows["S7"].left == 0
    assert rows["S8"].opened == 1
    assert rows["S8"].left_percent == 0


def test_period_and_csv_and_roles():
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    start = period_start("today", now, "Europe/Moscow")
    assert start == datetime(2026, 11, 17, 21, 0, tzinfo=timezone.utc)
    assert period_start("7d", now, "Europe/Moscow") == datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    assert period_start(None, now, "Europe/Moscow") is None

    table = applications_csv(
        [
            {
                "id": 1,
                "created_at": "2026-11-18T17:14:00Z",
                "status": "new",
                "tg_user_id": 5,
                "tg_username": "nick",
                "tg_name": "Имя",
                "consent_at": "2026-11-18T17:00:00Z",
                "problem": "Тревога",
                "free_text": "=1+1",
                "therapy_exp": "Впервые",
                "format": "онлайн",
                "tz": "UTC+3",
                "budget": "До 3 000 ₽",
                "slots": "выходные",
                "reply_in_chat": "нет",
            }
        ]
    )
    assert table.startswith("id;created_at;status;")
    assert "'=1+1" in table
    assert can_view("operator") and can_view("admin")
    assert can_export("admin") and not can_export("operator")
    assert can_delete("admin") and not can_delete("operator")
    assert not can_view(None)
