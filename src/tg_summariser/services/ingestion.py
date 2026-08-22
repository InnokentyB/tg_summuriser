from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.config import settings
from tg_summariser.models import Channel
from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.telegram_client import TelegramChannelPost, TelegramUserClient

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, tg_client: TelegramUserClient) -> None:
        self.tg_client = tg_client

    async def sync_channels(self, session: AsyncSession, limit_per_channel: int = 15) -> int:
        if not self.tg_client.is_connected():
            await self.tg_client.connect()
        if not self.tg_client.is_connected():
            return 0
        channel_repo = ChannelRepository(session)
        post_repo = PostRepository(session)
        channels = await channel_repo.list_telegram_channels()

        ingested = 0
        for index, channel in enumerate(channels):
            try:
                ingested += await asyncio.wait_for(
                    self.sync_channel(
                        session,
                        channel,
                        limit=limit_per_channel,
                        post_repo=post_repo,
                    ),
                    timeout=(
                        settings.telegram_channel_sync_timeout_seconds
                        if settings.telegram_channel_sync_timeout_seconds > 0
                        else 1
                    ),
                )
            except TimeoutError:
                logger.warning(
                    "Skipping channel sync due to timeout",
                    extra={"channel_id": channel.id, "channel_title": channel.title},
                )
            except Exception as exc:
                if _is_telegram_flood_wait(exc):
                    logger.warning(
                        "Skipping channel sync due to Telegram flood wait",
                        extra={"channel_id": channel.id, "channel_title": channel.title},
                    )
                    continue
                logger.warning(
                    "Failed to sync channel: %s",
                    _safe_error_summary(exc),
                    extra={
                        "channel_id": channel.id,
                        "channel_title": channel.title,
                        "error_type": type(exc).__name__,
                    },
                )
            finally:
                await channel_repo.mark_synced(channel.id)
                await session.flush()

            if index < len(channels) - 1 and settings.telegram_sync_delay_seconds > 0:
                await asyncio.sleep(settings.telegram_sync_delay_seconds)
        return ingested

    async def sync_channel(
        self,
        session: AsyncSession,
        channel: Channel,
        limit: int = 15,
        post_repo: PostRepository | None = None,
    ) -> int:
        if not self.tg_client.is_connected():
            await self.tg_client.connect()
        if not self.tg_client.is_connected():
            return 0

        repo = post_repo or PostRepository(session)
        posts, channel_ref = await self._iter_recent_posts_with_fallback(channel, limit)

        ingested = 0
        for item in posts:
            normalized = " ".join(item.text.split())
            _, created = await repo.create_post(
                channel_id=channel.id,
                telegram_message_id=item.message_id,
                raw_text=item.text,
                normalized_text=normalized,
                original_link=item.link,
                source_published_at=item.published_at,
            )
            if created:
                ingested += 1

        if posts:
            max_message_id = max(item.message_id for item in posts)
            try:
                await self.tg_client.mark_channel_posts_read(
                    channel_ref,
                    max_message_id=max_message_id,
                )
            except Exception as exc:
                if _is_telegram_flood_wait(exc):
                    logger.warning(
                        "Skipping read mark due to Telegram flood wait",
                        extra={"channel_id": channel.id, "max_message_id": max_message_id},
                    )
                    return ingested
                logger.warning(
                    "Failed to mark source channel messages as read: %s",
                    _safe_error_summary(exc),
                    extra={
                        "channel_id": channel.id,
                        "max_message_id": max_message_id,
                        "error_type": type(exc).__name__,
                    },
                )
        return ingested

    async def _iter_recent_posts_with_fallback(
        self,
        channel: Channel,
        limit: int,
    ) -> tuple[list[TelegramChannelPost], int | str]:
        refs: list[int | str] = [channel.telegram_chat_id]
        if channel.telegram_username:
            refs.append(channel.telegram_username)

        last_error: Exception | None = None
        for channel_ref in refs:
            try:
                posts = await self.tg_client.iter_recent_channel_posts(channel_ref, limit=limit)
                return posts, channel_ref
            except Exception as exc:
                if _is_telegram_flood_wait(exc):
                    raise
                last_error = exc
                logger.warning(
                    "Failed to fetch channel posts by reference, trying fallback if available",
                    extra={
                        "channel_id": channel.id,
                        "channel_ref": channel_ref,
                        "error_type": type(exc).__name__,
                    },
                )

        if last_error:
            raise last_error
        return [], channel.telegram_chat_id


def _is_telegram_flood_wait(exc: Exception) -> bool:
    return type(exc).__name__ == "FloodWaitError" or "FloodWaitError" in str(exc)


def _safe_error_summary(exc: Exception, limit: int = 240) -> str:
    summary = " ".join(str(exc).split())
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "..."
