from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey

from bot.config import Config
from bot.db import Application, Database
from bot.domain import (
    NO_USERNAME,
    Answers,
    CopySettings,
    card_text,
    screen_text,
    supplement_text,
    withdrawal_reply,
)
from bot.keyboards import survey_keyboard

logger = logging.getLogger(__name__)


class Flow(StatesGroup):
    active = State()
    done = State()


def copy_settings(config: Config) -> CopySettings:
    return CopySettings(
        operator_name=config.operator_name,
        operator_inn=config.operator_inn,
        operator_email=config.operator_email,
        response_time=config.response_time,
        privacy_url=config.privacy_url,
    )


async def show_screen(
    *,
    bot: Bot,
    chat_id: int,
    message_id: int | None,
    state: FSMContext,
    db: Database,
    config: Config,
    user_id: int,
    session_id: str,
    screen: str,
    answers: Answers,
    consent_at: str | None,
    application_id: int | None = None,
) -> int:
    text = screen_text(screen, answers, copy_settings(config))
    markup = survey_keyboard(screen, answers)
    new_id = message_id
    if message_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup,
            )
        except TelegramAPIError as exc:
            if "message is not modified" in str(exc).lower():
                new_id = message_id
            else:
                sent = await bot.send_message(chat_id, text, reply_markup=markup)
                new_id = sent.message_id
                logger.info("sent a new survey message because edit failed: %s", exc)
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=markup)
        new_id = sent.message_id

    await state.set_state(Flow.done if screen == "S8" else Flow.active)
    await state.update_data(
        screen=screen,
        session_id=session_id,
        answers=answers.to_dict(),
        consent_at=consent_at,
        prompt_message_id=new_id,
        application_id=application_id,
    )
    await db.track_screen(session_id, user_id, screen)
    return int(new_id or 0)


async def delete_messages(bot: Bot, chat_id: int | None, message_ids: list[int]) -> None:
    if not chat_id:
        return
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramAPIError:
            logger.info("curator message %s was not removed", message_id)


async def notify_curator(
    *,
    bot: Bot,
    db: Database,
    config: Config,
    application: Application,
) -> str | None:
    """Отправляет карточку в чат заявок. Возвращает текст ошибки для клиента, если чат недоступен."""
    if application.curator_message_id:
        return None
    if not config.applications_chat_id:
        return (
            "Заявка сохранена. Чат куратора пока не подключён — "
            "она видна в списке заявок."
        )
    text = card_text(
        created_at=application.created_at,
        tz_name=config.curator_tz,
        username=application.tg_username,
        full_name=application.tg_name,
        user_id=application.tg_user_id,
        answers=application.answers(),
        number=application.id,
        reply_in_chat=application.reply_in_chat,
    )
    try:
        sent = await bot.send_message(config.applications_chat_id, text)
    except TelegramAPIError:
        logger.exception("failed to deliver application %s", application.id)
        return (
            "Заявка сохранена, но отправить её в чат куратора не удалось. "
            "Она есть в списке заявок."
        )
    await db.set_curator_message(application.id, sent.message_id)
    return None


async def deliver_supplement(
    *,
    bot: Bot,
    db: Database,
    config: Config,
    application: Application,
    body: str,
    copy_from_chat: int | None = None,
    copy_message_id: int | None = None,
) -> bool:
    note = supplement_text(
        application_id=application.id,
        username=application.tg_username,
        full_name=application.tg_name,
        user_id=application.tg_user_id,
        body=body,
    )
    if len(note) > 4000:
        note = note[:3999] + "…"
    message_ids: list[int] = []
    delivered = False
    if config.applications_chat_id:
        try:
            sent = await bot.send_message(config.applications_chat_id, note)
            message_ids.append(sent.message_id)
            if copy_from_chat and copy_message_id:
                copied = await bot.copy_message(
                    config.applications_chat_id,
                    copy_from_chat,
                    copy_message_id,
                )
                message_ids.append(copied.message_id)
            delivered = True
        except TelegramAPIError:
            logger.exception("failed to deliver supplement for application %s", application.id)
    stored = body.strip() or "[сообщение без текста]"
    await db.add_supplement(application.id, stored, message_ids)
    return delivered


async def clear_user_state(state: FSMContext, user_id: int) -> None:
    """Сбрасывает анкету конкретного человека, не трогая состояние того, кто вызвал команду."""
    current = state.key
    key = StorageKey(
        bot_id=current.bot_id,
        chat_id=user_id,
        user_id=user_id,
        destiny=current.destiny,
    )
    await state.storage.set_state(key, None)
    await state.storage.set_data(key, {})


async def erase_user(
    *,
    bot: Bot,
    db: Database,
    config: Config,
    state: FSMContext,
    user_id: int,
    actor: str,
) -> int:
    message_ids = await db.curator_message_ids_for_user(user_id)
    removed = await db.delete_user(user_id)
    await db.audit(
        actor=actor,
        action="delete_user",
        target=str(user_id),
        details={"applications": removed},
    )
    await clear_user_state(state, user_id)
    await delete_messages(bot, config.applications_chat_id, message_ids)
    return removed


def user_withdrawal_text(removed: int) -> str:
    return withdrawal_reply(removed=removed)


def stored_username(username: str | None, reply_in_chat: bool) -> str:
    if reply_in_chat or not username:
        return NO_USERNAME
    return username
