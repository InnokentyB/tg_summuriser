from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot

from tg_summariser.db import session_scope
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import ChannelRepository, UserRepository
from tg_summariser.services.scoring import RelevanceScorer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChannelOnboardingTask:
    channel_id: int
    telegram_user_id: int


class ChannelOnboardingQueue:
    def __init__(self, bot: Bot, ingestion_service: IngestionService) -> None:
        self.bot = bot
        self.ingestion_service = ingestion_service
        self.queue: asyncio.Queue[ChannelOnboardingTask] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.pending_channel_ids: set[int] = set()
        self._stop_sentinel = ChannelOnboardingTask(channel_id=-1, telegram_user_id=-1)

    async def start(self) -> None:
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker(), name="channel-onboarding-worker")

    async def stop(self) -> None:
        if not self.worker_task:
            return
        await self.queue.put(self._stop_sentinel)
        await self.worker_task
        self.worker_task = None

    async def enqueue(self, channel_id: int, telegram_user_id: int) -> bool:
        if channel_id in self.pending_channel_ids:
            return False
        self.pending_channel_ids.add(channel_id)
        await self.queue.put(ChannelOnboardingTask(channel_id=channel_id, telegram_user_id=telegram_user_id))
        return True

    async def _worker(self) -> None:
        while True:
            task = await self.queue.get()
            if task == self._stop_sentinel:
                self.queue.task_done()
                break

            try:
                await self._process_task(task)
            except Exception:
                logger.exception("Channel onboarding task failed", extra={"channel_id": task.channel_id})
                await self.bot.send_message(
                    task.telegram_user_id,
                    "Не удалось обработать добавленный канал. Попробуйте еще раз чуть позже.",
                )
            finally:
                self.pending_channel_ids.discard(task.channel_id)
                self.queue.task_done()

    async def _process_task(self, task: ChannelOnboardingTask) -> None:
        async with session_scope() as session:
            channel = await ChannelRepository(session).get_by_id(task.channel_id)
            if not channel:
                await self.bot.send_message(
                    task.telegram_user_id,
                    "Канал не найден в базе. Добавьте его заново.",
                )
                return

            user = await UserRepository(session).get_or_create(task.telegram_user_id)
            synced = await self.ingestion_service.sync_channel(session, channel)
            processor = PostProcessor(AIPipeline(), Deduplicator(), RelevanceScorer())
            processed = await processor.process_pending(session, user.id)
            sent = await DigestService(self.bot).send_channel_welcome_digest(
                session=session,
                user_id=user.id,
                telegram_id=task.telegram_user_id,
                channel_id=channel.id,
                channel_title=channel.title,
            )

        await self.bot.send_message(
            task.telegram_user_id,
            f"Канал '{channel.title}' обработан.\n"
            f"Импортировано постов: {synced}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в стартовое саммари: {sent}",
        )

