from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from tg_summariser.bootstrap import init_db
from tg_summariser.bot.commands import BOT_COMMANDS
from tg_summariser.bot.handlers import register_handlers
from tg_summariser.config import settings
from tg_summariser.db import engine
from tg_summariser.scheduler import build_scheduler
from tg_summariser.services.channels import ChannelService
from tg_summariser.services.channel_onboarding_queue import ChannelOnboardingQueue
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
    await bot.set_my_commands(BOT_COMMANDS)
    dispatcher = Dispatcher()
    ingestion_service = IngestionService(tg_client)
    onboarding_queue = ChannelOnboardingQueue(bot=bot, ingestion_service=ingestion_service)
    await onboarding_queue.start()
    dispatcher.include_router(
        register_handlers(
            ChannelService(tg_client),
            onboarding_queue,
        )
    )

    scheduler = build_scheduler(bot, tg_client)
    scheduler.start()

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await onboarding_queue.stop()
        await tg_client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
