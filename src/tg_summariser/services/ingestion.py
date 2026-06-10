from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import Channel
from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.telegram_client import TelegramUserClient

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
        channels = await channel_repo.list_channels()

        ingested = 0
        for channel in channels:
            ingested += await self.sync_channel(session, channel, limit=limit_per_channel, post_repo=post_repo)
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
        posts = await self.tg_client.iter_recent_channel_posts(
            channel.telegram_username or channel.telegram_chat_id,
            limit=limit,
        )

        ingested = 0
        for item in posts:
            normalized = " ".join(item.text.split())
            _, created = await repo.create_post(
                channel_id=channel.id,
                telegram_message_id=item.message_id,
                raw_text=item.text,
                normalized_text=normalized,
                original_link=item.link,
            )
            if created:
                ingested += 1

        if posts:
            max_message_id = max(item.message_id for item in posts)
            try:
                await self.tg_client.mark_channel_posts_read(
                    channel.telegram_username or channel.telegram_chat_id,
                    max_message_id=max_message_id,
                )
            except Exception:
                logger.exception(
                    "Failed to mark source channel messages as read",
                    extra={"channel_id": channel.id, "max_message_id": max_message_id},
                )
        return ingested
