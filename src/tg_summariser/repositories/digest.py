from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import Digest, DigestItem


class DigestRepository:
    """Persistence operations for generated digests and item ranking."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def create_digest(self, user_id: int, scheduled_for: datetime) -> Digest:
        """Create a new digest delivery run for the specified user."""
        digest = Digest(user_id=user_id, scheduled_for=scheduled_for)
        self.session.add(digest)
        await self.session.flush()
        return digest

    async def add_item(self, digest_id: int, post_id: int, rank: int) -> DigestItem:
        """Record an included post and its display rank within a digest."""
        item = DigestItem(digest_id=digest_id, post_id=post_id, rank=rank)
        self.session.add(item)
        await self.session.flush()
        return item
