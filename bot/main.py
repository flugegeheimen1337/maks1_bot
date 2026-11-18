from __future__ import annotations

import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent, Message
from redis.asyncio import Redis

from bot.config import Config, load_config
from bot.db import Database
from bot.handlers.admin import router as admin_router
from bot.handlers.survey import router as survey_router

logger = logging.getLogger(__name__)


class RoleMiddleware(BaseMiddleware):
    """Прокидывает роль сотрудника в фильтры команд."""

    def __init__(self, config: Config) -> None:
        self.config = config

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        data["staff_role"] = self.config.role_of(user.id) if user is not None else None
        return await handler(event, data)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def build_dispatcher(config: Config, storage: RedisStorage) -> Dispatcher:
    dispatcher = Dispatcher(storage=storage)
    dispatcher["config"] = config
    role = RoleMiddleware(config)
    dispatcher.message.outer_middleware(role)
    dispatcher.callback_query.outer_middleware(role)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(survey_router)

    @dispatcher.errors()
    async def on_error(event: ErrorEvent, bot: Bot) -> None:
        logger.exception("update failed", exc_info=event.exception)
        update = event.update
        message = update.message
        callback_message = update.callback_query.message if update.callback_query else None
        if message is None and isinstance(callback_message, Message):
            message = callback_message
        if message is None or message.chat.type != "private":
            return
        try:
            await bot.send_message(
                message.chat.id,
                "Не получилось обработать сообщение. Отправьте /start, чтобы продолжить.",
            )
        except TelegramAPIError:
            logger.info("error notice was not delivered")

    return dispatcher


async def run() -> None:
    setup_logging()
    config = load_config()
    if config.applications_chat_id is None:
        logger.warning("APPLICATIONS_CHAT_ID не задан: карточки сохраняются только в базе")
    if not config.admin_ids:
        logger.warning("ADMIN_IDS пуст: админ-команды никому не доступны")

    database = Database(config.database_path)
    await database.connect()
    redis = Redis.from_url(config.redis_url)
    storage = RedisStorage(
        redis=redis,
        state_ttl=config.state_ttl_seconds,
        data_ttl=config.state_ttl_seconds,
    )
    bot = Bot(token=config.bot_token)
    dispatcher = build_dispatcher(config, storage)
    dispatcher["db"] = database
    logger.info("Бот анкетирования запущен")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await storage.close()
        await database.close()


def main() -> None:
    asyncio.run(run())
