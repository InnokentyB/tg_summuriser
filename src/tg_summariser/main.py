from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tg_summariser.bootstrap import init_db
from tg_summariser.bot.handlers import register_handlers
from tg_summariser.config import settings
from tg_summariser.db import engine
from tg_summariser.scheduler import build_scheduler
from tg_summariser.services.channels import ChannelService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.telegram_client import TelegramUserClient


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db(engine)

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required.")

    tg_client = TelegramUserClient()
    await tg_client.connect()
    if not tg_client.is_connected():
        logging.warning(
            "Telethon user client is not configured. Private channels and ingestion are disabled until Telegram session credentials are provided."
        )

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(register_handlers(ChannelService(tg_client), IngestionService(tg_client)))

    scheduler = build_scheduler(bot, tg_client)
    scheduler.start()

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await tg_client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
