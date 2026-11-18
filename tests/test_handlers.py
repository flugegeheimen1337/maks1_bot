from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from bot.config import Config
from bot.db import Database
from bot.domain import STALE_ALERT, Answers
from bot.handlers.survey import cmd_start, on_active_text, on_callback, on_followup, withdraw
from bot.services import Flow


@pytest.fixture
async def env(tmp_path):
    database = Database(str(tmp_path / "handlers.sqlite"))
    await database.connect()
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=10, user_id=10)
    state = FSMContext(storage=storage, key=key)
    config = Config(
        bot_token="token",
        redis_url="redis://localhost:6379/0",
        database_path=str(tmp_path / "handlers.sqlite"),
        applications_chat_id=None,
        admin_ids=frozenset({1}),
        operator_ids=frozenset({2}),
        operator_name="Иванов Иван",
        operator_inn="7700000000",
        operator_email="hello@example.com",
        response_time="рабочего дня",
        privacy_url="",
        curator_tz="Europe/Moscow",
    )
    yield database, state, config
    await database.close()


def _person() -> User:
    return User(id=10, is_bot=False, first_name="Анна", last_name="Иванова", username="nick")


def _user_message(text: str = ""):
    message = AsyncMock()
    message.text = text
    message.caption = None
    message.content_type = "text"
    message.chat.id = 10
    message.chat.type = "private"
    message.from_user = _person()
    message.answer = AsyncMock(return_value=SimpleNamespace(message_id=5))
    return message


def _telegram_message(text: str = "", message_id: int = 5) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=10, type="private"),
        from_user=_person(),
        text=text,
    )


async def test_start_opens_welcome(env):
    database, state, config = env
    message = _user_message("/start")
    await cmd_start(message, state, database, config)
    data = await state.get_data()
    assert data["screen"] == "S0"
    assert data["prompt_message_id"] == 5
    assert await state.get_state() == Flow.active.state
    sessions = await database.funnel_sessions()
    assert sessions == [{"S0"}]
    text = message.answer.await_args.args[0]
    assert "Практика" in text


async def test_stale_button_does_not_continue(env):
    database, state, config = env
    callback = AsyncMock()
    callback.data = "agree"
    callback.from_user = _person()
    callback.message = _telegram_message()
    callback.answer = AsyncMock()
    await on_callback(callback, state, AsyncMock(), database, config)
    callback.answer.assert_awaited_with(STALE_ALERT, show_alert=True)


async def test_submit_saves_card_for_curator(env):
    database, state, config = env
    config = replace(config, applications_chat_id=-100)
    answers = Answers(
        problem="c_anx",
        free_text="Трясёт перед выступлениями",
        therapy_exp="e_fail",
        format="f_online",
        tz="tz3",
        budget="p3",
        slots=["t_eve", "t_week"],
    )
    await state.set_state(Flow.active)
    await state.set_data(
        {
            "screen": "S7",
            "session_id": "ready",
            "answers": answers.to_dict(),
            "consent_at": "2026-11-18T16:00:00Z",
            "prompt_message_id": 5,
            "application_id": None,
        }
    )
    callback = AsyncMock()
    callback.data = "submit"
    callback.from_user = _person()
    callback.message = _telegram_message()
    callback.answer = AsyncMock()
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=77))
    await on_callback(callback, state, bot, database, config)
    application = await database.latest_for_user(10)
    assert application is not None
    assert application.curator_message_id == 77
    assert application.tg_username == "nick"
    assert await state.get_state() == Flow.done.state
    card = bot.send_message.await_args.args[1]
    assert "НОВАЯ ЗАЯВКА" in card
    assert "@nick" in card
    assert "Трясёт перед выступлениями" in card

    followup = _user_message("Можно вечером в четверг.")
    await on_followup(followup, state, bot, database, config)
    notes = await database.list_supplements(application.id)
    assert notes[-1].text == "Можно вечером в четверг."
    assert "дополнение" in followup.answer.await_args.args[0].casefold()


async def test_comment_advances_to_experience(env):
    database, state, config = env
    await state.set_state(Flow.active)
    await state.set_data(
        {
            "screen": "S2",
            "session_id": "session",
            "answers": {"problem": "c_work"},
            "consent_at": "2026-11-18T10:00:00Z",
            "prompt_message_id": 7,
            "application_id": None,
        }
    )
    message = _user_message("Стало трудно собираться на работу.")
    bot = AsyncMock()
    await on_active_text(message, state, bot, database, config)
    data = await state.get_data()
    assert data["screen"] == "S3"
    assert data["answers"]["free_text"] == "Стало трудно собираться на работу."
    bot.edit_message_text.assert_awaited()


async def test_withdrawal_erases_saved_application(env):
    database, state, config = env
    await database.create_application(
        session_id="done",
        user_id=10,
        username="nick",
        full_name="Анна Иванова",
        consent_at="2026-11-18T10:00:00Z",
        answers=Answers(
            problem="c_work",
            free_text="текст",
            therapy_exp="e_first",
            format="f_offline",
            tz="—",
            budget="p0",
            slots=["t_day"],
        ),
        reply_in_chat=False,
    )
    await state.set_state(Flow.done)
    await state.update_data(screen="S8", secret="не должно пережить отзыв")
    message = _user_message("Отзываю согласие")
    await withdraw(message, state, AsyncMock(), database, config)
    assert await database.latest_for_user(10) is None
    assert await state.get_state() is None
    assert await state.get_data() == {}
    assert "удален" in message.answer.await_args.args[0]
