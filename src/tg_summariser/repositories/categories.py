from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import Post, UserCategoryPreference


class UserCategoryPreferenceRepository:
    """Persistence operations for user topic/category preference filters."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def enabled_categories(self, user_id: int) -> list[str]:
        """Fetch list of explicitly enabled categories for the user."""
        result = await self.session.execute(
            select(UserCategoryPreference.category)
            .where(
                UserCategoryPreference.user_id == user_id,
                UserCategoryPreference.is_enabled.is_(True),
            )
            .order_by(UserCategoryPreference.category)
        )
        return [row[0] for row in result.all()]

    async def all_preferences(self, user_id: int) -> list[UserCategoryPreference]:
        """Return all category preference records for the user."""
        result = await self.session.execute(
            select(UserCategoryPreference)
            .where(UserCategoryPreference.user_id == user_id)
            .order_by(UserCategoryPreference.category)
        )
        return list(result.scalars())

    async def set_enabled(self, user_id: int, category: str, is_enabled: bool) -> UserCategoryPreference:
        """Set enabled/disabled flag for the given user category preference."""
        normalized = category.strip()
        result = await self.session.execute(
            select(UserCategoryPreference).where(
                UserCategoryPreference.user_id == user_id,
                UserCategoryPreference.category == normalized,
            ).order_by(UserCategoryPreference.id.asc())
        )
        preference = result.scalars().first()
        if preference:
            preference.is_enabled = is_enabled
            preference.updated_at = datetime.utcnow()
            return preference

        preference = UserCategoryPreference(
            user_id=user_id,
            category=normalized,
            is_enabled=is_enabled,
        )
        self.session.add(preference)
        await self.session.flush()
        return preference

    async def clear(self, user_id: int) -> int:
        """Clear all category preferences for a user, reverting to show-all mode."""
        preferences = await self.all_preferences(user_id)
        for preference in preferences:
            await self.session.delete(preference)
        return len(preferences)

    async def known_categories(self, limit: int = 100) -> list[str]:
        """Return list of distinct categories currently present in ingested posts."""
        result = await self.session.execute(
            select(Post.category)
            .where(Post.category.is_not(None))
            .group_by(Post.category)
            .order_by(func.count(Post.id).desc(), Post.category.asc())
            .limit(limit)
        )
        return [row[0] for row in result.all() if row[0]]
