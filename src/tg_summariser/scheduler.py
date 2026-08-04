from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from tg_summariser.config import settings
from tg_summariser.db import session_scope
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import UserRepository
from tg_summariser.services.scoring import RelevanceScorer
from tg_summariser.services.telegram_client import TelegramUserClient
from tg_summariser.services.tgarticles_importer import TGArticlesImportService

logger = logging.getLogger(__name__)


def build_scheduler(bot: Bot, tg_client: TelegramUserClient) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def import_articles(session) -> int:
        article_importer = TGArticlesImportService.from_settings()
        if not article_importer:
            return 0
        return await article_importer.import_recent(session)

    async def run_article_import_job() -> None:
        logger.info("Scheduled article import started")
        async with session_scope() as session:
            if not settings.owner_telegram_id:
                logger.warning("Scheduled article import skipped: OWNER_TELEGRAM_ID is not configured")
                return
            user = await UserRepository(session).get_or_create(settings.owner_telegram_id)
            imported = await import_articles(session)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            logger.info(
                "Scheduled article import finished: imported=%s processed=%s",
                imported,
                processed,
            )

    async def run_digest_job() -> None:
        logger.info("Scheduled digest started")
        try:
            async with session_scope() as session:
                if not settings.owner_telegram_id:
                    logger.warning("Scheduled digest skipped: OWNER_TELEGRAM_ID is not configured")
                    return
                user = await UserRepository(session).get_or_create(settings.owner_telegram_id)
                synced = await IngestionService(tg_client).sync_channels(session)
                imported = await import_articles(session)
                processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
                processed = await processor.process_pending(session, user.id)
                sent = await DigestService(bot).send_digest(session, user.id, user.telegram_id)
                logger.info(
                    "Scheduled digest finished: synced=%s imported=%s processed=%s sent=%s",
                    synced,
                    imported,
                    processed,
                    sent,
                )
        except Exception as exc:
            logger.exception("Scheduled digest failed")
            if settings.owner_telegram_id:
                await bot.send_message(
                    settings.owner_telegram_id,
                    f"Плановый дайджест упал до отправки: {type(exc).__name__}: {exc}",
                )
            raise

    for import_time in settings.tgarticles_import_times:
        hour_str, minute_str = import_time.split(":")
        scheduler.add_job(
            run_article_import_job,
            "cron",
            hour=int(hour_str),
            minute=int(minute_str),
            id=f"tgarticles-import-{import_time}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )

    for digest_time in settings.digest_times:
        hour_str, minute_str = digest_time.split(":")
        scheduler.add_job(
            run_digest_job,
            "cron",
            hour=int(hour_str),
            minute=int(minute_str),
            id=f"digest-{digest_time}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )

    return scheduler
