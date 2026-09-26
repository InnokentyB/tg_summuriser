from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import User


class UserRepository:
    """Persistence operations for User records."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def get_or_create(self, telegram_id: int, username: str | None = None) -> User:
        """Fetch existing user by telegram_id or create a new user entity."""
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id).order_by(User.id.asc())
        )
        user = result.scalars().first()
        if user:
            if username and user.username != username:
                user.username = username
            return user

        user = User(telegram_id=telegram_id, username=username)
        self.session.add(user)
        await self.session.flush()
        return user
