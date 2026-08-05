from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot

from tg_summariser.config import settings
from tg_summariser.db import session_scope
from tg_summariser.models import PostStatus
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import (
    ChannelOnboardingJobRepository,
    ChannelRepository,
    PostRepository,
    UserRepository,
)
from tg_summariser.services.scoring import RelevanceScorer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChannelOnboardingTask:
    channel_id: int
    telegram_user_id: int


class ChannelOnboardingQueue:
    def __init__(self, bot: Bot, ingestion_service: IngestionService, persist_tasks: bool = True) -> None:
        self.bot = bot
        self.ingestion_service = ingestion_service
        self.persist_tasks = persist_tasks
        self.queue: asyncio.Queue[ChannelOnboardingTask] = asyncio.Queue()
        self.worker_task: asyncio.Task | None = None
        self.pending_channel_ids: set[int] = set()
        self._stop_sentinel = ChannelOnboardingTask(channel_id=-1, telegram_user_id=-1)

    async def start(self) -> None:
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker(), name="channel-onboarding-worker")
        if self.persist_tasks:
            await self._recover_persisted_tasks()

    async def stop(self) -> None:
        if not self.worker_task:
            return
        await self.queue.put(self._stop_sentinel)
        await self.worker_task
        self.worker_task = None

    async def enqueue(self, channel_id: int, telegram_user_id: int) -> bool:
        if channel_id in self.pending_channel_ids:
            return False
        if self.persist_tasks:
            await self._ensure_worker_running()
            async with session_scope() as session:
                await ChannelOnboardingJobRepository(session).enqueue(
                    channel_id=channel_id,
                    telegram_user_id=telegram_user_id,
                )
        self.pending_channel_ids.add(channel_id)
        await self.queue.put(ChannelOnboardingTask(channel_id=channel_id, telegram_user_id=telegram_user_id))
        return True

    async def _ensure_worker_running(self) -> None:
        if self.worker_task and not self.worker_task.done():
            return
        self.worker_task = asyncio.create_task(self._worker(), name="channel-onboarding-worker")

    async def _recover_persisted_tasks(self) -> None:
        recovered_tasks: list[ChannelOnboardingTask] = []
        async with session_scope() as session:
            job_repo = ChannelOnboardingJobRepository(session)
            if settings.owner_telegram_id:
                for channel in await ChannelRepository(session).channels_without_posts():
                    await job_repo.enqueue(channel.id, settings.owner_telegram_id)

            for job in await job_repo.recoverable_jobs():
                recovered_tasks.append(
                    ChannelOnboardingTask(
                        channel_id=job.channel_id,
                        telegram_user_id=job.telegram_user_id,
                    )
                )

        for task in recovered_tasks:
            if task.channel_id in self.pending_channel_ids:
                continue
            self.pending_channel_ids.add(task.channel_id)
            await self.queue.put(task)

        if recovered_tasks:
            logger.info("Recovered channel onboarding tasks", extra={"count": len(recovered_tasks)})

    async def _worker(self) -> None:
        while True:
            task = await self.queue.get()
            if task == self._stop_sentinel:
                self.queue.task_done()
                break

            try:
                logger.info("Channel onboarding task started", extra={"channel_id": task.channel_id})
                await self._process_task(task)
                logger.info("Channel onboarding task completed", extra={"channel_id": task.channel_id})
            except Exception as exc:
                logger.exception("Channel onboarding task failed", extra={"channel_id": task.channel_id})
                if self.persist_tasks:
                    async with session_scope() as session:
                        await ChannelOnboardingJobRepository(session).mark_failed(task.channel_id, str(exc))
                await self.bot.send_message(
                    task.telegram_user_id,
                    "Не удалось обработать добавленный канал. Попробуйте еще раз чуть позже.",
                )
            finally:
                self.pending_channel_ids.discard(task.channel_id)
                self.queue.task_done()

    async def _process_task(self, task: ChannelOnboardingTask) -> None:
        async with session_scope() as session:
            job_repo = ChannelOnboardingJobRepository(session)
            if self.persist_tasks:
                await job_repo.mark_processing(task.channel_id)
            channel = await ChannelRepository(session).get_by_id(task.channel_id)
            if not channel:
                if self.persist_tasks:
                    await job_repo.mark_failed(task.channel_id, "Channel not found")
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
                notify_empty=False,
            )
            diagnostics = ""
            if sent == 0:
                diagnostics = await self._build_empty_digest_diagnostics(session, channel.id)
            if self.persist_tasks:
                await job_repo.mark_completed(task.channel_id)

        await self.bot.send_message(
            task.telegram_user_id,
            f"Канал '{channel.title}' обработан.\n"
            f"Импортировано постов: {synced}\n"
            f"Обработано AI: {processed}\n"
            f"Отправлено в стартовое саммари: {sent}"
            f"{diagnostics}",
        )

    async def _build_empty_digest_diagnostics(self, session, channel_id: int) -> str:
        post_repo = PostRepository(session)
        counts = await post_repo.channel_status_counts(channel_id)
        sent_count = await post_repo.sent_count_for_channel(channel_id)
        hidden_examples = await post_repo.hidden_posts_for_channel(channel_id, limit=3)

        lines = [
            "",
            "",
            "Посты нашлись, но фильтр не выбрал их для стартового саммари.",
            (
                "Статусы: "
                f"готово {counts.get(PostStatus.processed, 0)}, "
                f"скрыто {counts.get(PostStatus.hidden, 0)}, "
                f"в очереди {counts.get(PostStatus.pending, 0)}, "
                f"уже отправлено {sent_count}."
            ),
        ]
        if hidden_examples:
            lines.append("Лучшие скрытые кандидаты:")
            for post in hidden_examples:
                summary = self._shorten(post.summary or post.raw_text, 120)
                explanation = self._shorten(post.explanation or "Без объяснения фильтра.", 160)
                lines.append(f"• {summary}\n  Причина: {explanation}")
        return "\n".join(lines)

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "..."
