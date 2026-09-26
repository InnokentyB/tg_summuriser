from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import Channel, ChannelOnboardingJob, Post
from tg_summariser.repositories.utils import normalize_telegram_chat_id


class ChannelRepository:
    """Persistence operations for Telegram and external content channels."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def upsert_channel(
        self,
        telegram_chat_id: int,
        title: str,
        telegram_username: str | None,
        is_private: bool,
        source_kind: str = "telegram_channel",
    ) -> Channel:
        """Create or update channel record with normalized identifier and details."""
        normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        channel = None

        if telegram_username:
            result = await self.session.execute(
                select(Channel)
                .where(Channel.telegram_username == telegram_username)
                .order_by(Channel.id.asc())
            )
            channel = result.scalars().first()

        if channel is None:
            result = await self.session.execute(
                select(Channel)
                .where(Channel.telegram_chat_id == normalized_chat_id)
                .order_by(Channel.id.asc())
            )
            channel = result.scalars().first()

        if channel:
            channel.telegram_chat_id = normalized_chat_id
            channel.title = title
            channel.telegram_username = telegram_username
            channel.is_private = is_private
            channel.source_kind = source_kind
            channel.is_active = True
            return channel

        channel = Channel(
            telegram_chat_id=normalized_chat_id,
            title=title,
            telegram_username=telegram_username,
            is_private=is_private,
            source_kind=source_kind,
        )
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def list_channels(self) -> list[Channel]:
        """Return all channels ordered alphabetically by title."""
        result = await self.session.execute(select(Channel).order_by(Channel.title))
        return list(result.scalars())

    async def list_telegram_channels(self) -> list[Channel]:
        """Return active native Telegram channels."""
        result = await self.session.execute(
            select(Channel)
            .where(Channel.is_active.is_(True), Channel.source_kind == "telegram_channel")
            .order_by(Channel.title)
        )
        return list(result.scalars())

    async def count_telegram_channels(self) -> int:
        """Count active native Telegram channels."""
        result = await self.session.execute(
            select(func.count(Channel.id)).where(
                Channel.is_active.is_(True),
                Channel.source_kind == "telegram_channel",
            )
        )
        return int(result.scalar_one())

    async def mark_synced(self, channel_id: int, synced_at: datetime | None = None) -> None:
        """Update last synchronization timestamp for the specified channel."""
        await self.session.execute(
            update(Channel)
            .where(Channel.id == channel_id)
            .values(last_synced_at=synced_at or datetime.utcnow())
        )

    async def get_by_id(self, channel_id: int) -> Channel | None:
        """Fetch channel record by primary key id."""
        result = await self.session.execute(select(Channel).where(Channel.id == channel_id))
        return result.scalar_one_or_none()

    async def channels_without_posts(self) -> list[Channel]:
        """Find active channels that currently have zero ingested posts."""
        result = await self.session.execute(
            select(Channel)
            .outerjoin(Post)
            .outerjoin(ChannelOnboardingJob)
            .where(
                Channel.is_active.is_(True),
                Channel.source_kind == "telegram_channel",
                or_(ChannelOnboardingJob.id.is_(None), ChannelOnboardingJob.status != "completed"),
            )
            .group_by(Channel.id)
            .having(func.count(Post.id) == 0)
            .order_by(Channel.created_at.asc())
        )
        return list(result.scalars())
