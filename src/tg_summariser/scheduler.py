from __future__ import annotations

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


def build_scheduler(bot: Bot, tg_client: TelegramUserClient) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def import_articles(session) -> None:
        article_importer = TGArticlesImportService.from_settings()
        if not article_importer:
            return
        await article_importer.import_recent(session)

    async def run_article_import_job() -> None:
        async with session_scope() as session:
            if not settings.owner_telegram_id:
                return
            user = await UserRepository(session).get_or_create(settings.owner_telegram_id)
            await import_articles(session)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            await processor.process_pending(session, user.id)

    async def run_digest_job() -> None:
        async with session_scope() as session:
            if not settings.owner_telegram_id:
                return
            user = await UserRepository(session).get_or_create(settings.owner_telegram_id)
            await IngestionService(tg_client).sync_channels(session)
            await import_articles(session)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            await processor.process_pending(session, user.id)
            await DigestService(bot).send_digest(session, user.id, user.telegram_id)

    for import_time in settings.tgarticles_import_times:
        hour_str, minute_str = import_time.split(":")
        scheduler.add_job(
            run_article_import_job,
            "cron",
            hour=int(hour_str),
            minute=int(minute_str),
            id=f"tgarticles-import-{import_time}",
            replace_existing=True,
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
        )

    return scheduler
