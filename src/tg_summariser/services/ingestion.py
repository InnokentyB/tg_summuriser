from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.telegram_client import TelegramUserClient


class IngestionService:
    def __init__(self, tg_client: TelegramUserClient) -> None:
        self.tg_client = tg_client

    async def sync_channels(self, session: AsyncSession, limit_per_channel: int = 15) -> int:
        if not self.tg_client.is_connected():
            return 0
        channel_repo = ChannelRepository(session)
        post_repo = PostRepository(session)
        channels = await channel_repo.list_channels()

        ingested = 0
        for channel in channels:
            posts = await self.tg_client.iter_recent_channel_posts(
                channel.telegram_username or channel.telegram_chat_id,
                limit=limit_per_channel,
            )
            for item in posts:
                normalized = " ".join(item.text.split())
                _, created = await post_repo.create_post(
                    channel_id=channel.id,
                    telegram_message_id=item.message_id,
                    raw_text=item.text,
                    normalized_text=normalized,
                    original_link=item.link,
                )
                if created:
                    ingested += 1
        return ingested
