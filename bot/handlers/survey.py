from __future__ import annotations

from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.db import Database
from bot.domain import (
    ADD_INFO_BUTTON,
    OUT_OF_FLOW,
    RESTART_BUTTON,
    STALE_ALERT,
    Answers,
    apply,
    is_withdrawal,
    missing_screen,
    screen_text,
    utc_now_iso,
    validate_free_text,
)
from bot.keyboards import survey_keyboard
from bot.services import (
    Flow,
    copy_settings,
    deliver_supplement,
    erase_user,
    notify_curator,
    show_screen,
    stored_username,
    user_withdrawal_text,
)

router = Router(name="survey")


def _private(message: Message | None) -> bool:
    return bool(message and message.chat and message.chat.type == "private" and message.from_user)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    if not _private(message):
        return
    assert message.from_user is not None
    session_id = uuid4().hex
    answers = Answers()
    await state.set_state(Flow.active)
    await state.set_data(
        {
            "screen": "S0",
            "session_id": session_id,
            "answers": answers.to_dict(),
            "consent_at": None,
            "prompt_message_id": None,
            "application_id": None,
        }
    )
    sent = await message.answer(
        screen_text("S0", answers, copy_settings(config)),
        reply_markup=survey_keyboard("S0", answers),
    )
    await state.update_data(prompt_message_id=sent.message_id)
    await db.track_screen(session_id, message.from_user.id, "S0")


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    if not _private(message):
        return
    assert message.from_user is not None
    if await state.get_state() == Flow.active.state:
        await message.answer("Сначала отправьте текущую анкету. Добавить информацию можно после неё.")
        return
    await _prompt_supplement(message, state, bot, db, message.from_user.id)


@router.message(F.text.func(is_withdrawal))
async def withdraw(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if not _private(message):
        return
    assert message.from_user is not None
    removed = await erase_user(
        bot=bot,
        db=db,
        config=config,
        state=state,
        user_id=message.from_user.id,
        actor=f"user:{message.from_user.id}",
    )
    await message.answer(user_withdrawal_text(removed))


@router.callback_query(F.data.regexp(r"^[^:]+$"))
async def on_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    message = callback.message
    if not isinstance(message, Message) or not _private(message):
        await callback.answer()
        return
    assert callback.from_user is not None

    current = await state.get_state()
    data = await state.get_data()
    if callback.data == "restart":
        await callback.answer()
        await _restart(
            message,
            state,
            bot,
            db,
            config,
            callback.from_user.id,
            message_id=message.message_id,
        )
        return
    if callback.data == "add_info":
        if current == Flow.active.state:
            await callback.answer(STALE_ALERT, show_alert=True)
            return
        await callback.answer()
        await _prompt_supplement(message, state, bot, db, callback.from_user.id)
        return
    if current != Flow.active.state or data.get("prompt_message_id") != message.message_id:
        await callback.answer(STALE_ALERT, show_alert=True)
        return

    screen = str(data.get("screen") or "")
    answers = Answers.from_dict(data.get("answers"))
    result = apply(
        screen,
        callback.data or "",
        answers,
        has_username=bool(callback.from_user.username),
    )
    if result is None:
        await callback.answer()
        return
    if result.alert and result.screen == screen and not result.submit:
        await callback.answer(result.alert, show_alert=True)
        return

    consent_at = data.get("consent_at")
    if callback.data == "agree" and not consent_at:
        consent_at = utc_now_iso()

    await callback.answer()
    if result.submit:
        await _submit(
            callback=callback,
            state=state,
            bot=bot,
            db=db,
            config=config,
            answers=result.answers,
            consent_at=consent_at,
            mode=result.submit_mode or "username",
        )
        return

    await show_screen(
        bot=bot,
        chat_id=message.chat.id,
        message_id=message.message_id,
        state=state,
        db=db,
        config=config,
        user_id=callback.from_user.id,
        session_id=str(data.get("session_id") or ""),
        screen=result.screen,
        answers=result.answers,
        consent_at=consent_at,
        application_id=data.get("application_id"),
    )


async def _submit(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db: Database,
    config: Config,
    answers: Answers,
    consent_at: str | None,
    mode: str,
) -> None:
    message = callback.message
    if not isinstance(message, Message) or callback.from_user is None:
        return
    data = await state.get_data()
    gap = missing_screen(answers)
    if gap:
        await show_screen(
            bot=bot,
            chat_id=message.chat.id,
            message_id=message.message_id,
            state=state,
            db=db,
            config=config,
            user_id=callback.from_user.id,
            session_id=str(data.get("session_id") or ""),
            screen=gap,
            answers=answers,
            consent_at=consent_at,
            application_id=data.get("application_id"),
        )
        return

    reply_in_chat = mode == "nochat" or not callback.from_user.username
    username = stored_username(callback.from_user.username, reply_in_chat)
    moment = consent_at or utc_now_iso()
    application = await db.create_application(
        session_id=str(data.get("session_id") or ""),
        user_id=callback.from_user.id,
        username=username,
        full_name=_full_name(callback),
        consent_at=moment,
        answers=answers,
        reply_in_chat=reply_in_chat,
    )
    warning = await notify_curator(bot=bot, db=db, config=config, application=application)
    await show_screen(
        bot=bot,
        chat_id=message.chat.id,
        message_id=message.message_id,
        state=state,
        db=db,
        config=config,
        user_id=callback.from_user.id,
        session_id=application.session_id,
        screen="S8",
        answers=answers,
        consent_at=moment,
        application_id=application.id,
    )
    if warning:
        await message.answer(warning)


async def _restart(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: Database,
    config: Config,
    user_id: int,
    message_id: int | None,
) -> None:
    session_id = uuid4().hex
    await show_screen(
        bot=bot,
        chat_id=message.chat.id,
        message_id=message_id,
        state=state,
        db=db,
        config=config,
        user_id=user_id,
        session_id=session_id,
        screen="S0",
        answers=Answers(),
        consent_at=None,
        application_id=None,
    )


async def _prompt_supplement(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: Database,
    user_id: int,
) -> None:
    application = await db.latest_for_user(user_id)
    if application is None:
        await bot.send_message(
            message.chat.id,
            "Заявки пока нет. Нажмите «Начать снова» или отправьте /start.",
        )
        return
    await state.set_state(Flow.adding)
    await state.update_data(screen="S8", application_id=application.id)
    await bot.send_message(
        message.chat.id,
        "Напишите, что хотите добавить или уточнить. "
        "Следующее сообщение уйдёт куратору вместе с заявкой.",
    )


def _full_name(callback: CallbackQuery) -> str:
    user = callback.from_user
    if user is None:
        return "—"
    parts = [part for part in (user.first_name, user.last_name) if part]
    return " ".join(parts) if parts else "—"


@router.message(StateFilter(Flow.active), F.text, ~F.text.startswith("/"))
async def on_active_text(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if not _private(message):
        return
    assert message.from_user is not None
    data = await state.get_data()
    if data.get("screen") != "S2":
        await message.answer(OUT_OF_FLOW)
        return
    cleaned, error = validate_free_text(message.text or "")
    if error:
        await message.answer(error)
        return
    answers = Answers.from_dict(data.get("answers"))
    answers.free_text = cleaned
    await show_screen(
        bot=bot,
        chat_id=message.chat.id,
        message_id=data.get("prompt_message_id"),
        state=state,
        db=db,
        config=config,
        user_id=message.from_user.id,
        session_id=str(data.get("session_id") or ""),
        screen="S3",
        answers=answers,
        consent_at=data.get("consent_at"),
        application_id=data.get("application_id"),
    )


class AfterSurvey(Filter):
    """Сообщения после финала и после того, как 24-часовой черновик уже истёк."""

    async def __call__(self, message: Message, state: FSMContext) -> bool:
        if (message.text or "").startswith("/"):
            return False
        current = await state.get_state()
        return current in {None, Flow.done.state, Flow.adding.state}


@router.message(AfterSurvey())
async def on_followup(message: Message, state: FSMContext, bot: Bot, db: Database, config: Config) -> None:
    if not _private(message):
        return
    assert message.from_user is not None
    current = await state.get_state()
    if current == Flow.active.state:
        return
    text = (message.text or "").strip()
    if text.casefold() == RESTART_BUTTON.casefold():
        data = await state.get_data()
        await _restart(
            message,
            state,
            bot,
            db,
            config,
            message.from_user.id,
            message_id=data.get("prompt_message_id"),
        )
        return
    if text.casefold() == ADD_INFO_BUTTON.casefold():
        await _prompt_supplement(message, state, bot, db, message.from_user.id)
        return
    application = await db.latest_for_user(message.from_user.id)
    if application is None:
        await message.answer(OUT_OF_FLOW)
        return
    body = message.text or message.caption or ""
    if not body.strip():
        body = f"[{message.content_type}]"
    copy_message_id = None if message.content_type == "text" else message.message_id
    delivered = await deliver_supplement(
        bot=bot,
        db=db,
        config=config,
        application=application,
        body=body,
        copy_from_chat=None if copy_message_id is None else message.chat.id,
        copy_message_id=copy_message_id,
    )
    if await state.get_state() == Flow.adding.state:
        await state.set_state(Flow.done)
    if delivered:
        await message.answer("Передала это куратору как дополнение к заявке.")
    else:
        await message.answer(
            "Сохранила дополнение к заявке. В чат куратора сейчас отправить не удалось."
        )


@router.message(StateFilter(Flow.active))
async def on_active_other(message: Message, state: FSMContext) -> None:
    if not _private(message):
        return
    data = await state.get_data()
    if data.get("screen") == "S2":
        await message.answer(
            "Здесь нужно текстовое сообщение до 500 символов. Или нажмите «Пропустить»."
        )
        return
    await message.answer(OUT_OF_FLOW)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    if not _private(message):
        return
    await message.answer(OUT_OF_FLOW)
