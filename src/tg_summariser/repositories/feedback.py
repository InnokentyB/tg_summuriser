from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.models import Channel, FeedbackValue, Post, UserFeedback


@dataclass(slots=True)
class PostReactionStat:
    """Aggregated reaction counters for a post."""

    post_id: int
    channel_id: int
    channel_title: str
    telegram_message_id: int
    total_reactions: int
    interested_reactions: int
    not_interested_reactions: int
    interested_ratio: float


class FeedbackRepository:
    """Persistence operations for user reactions and preference signals."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def add_feedback(self, user_id: int, post_id: int, value: FeedbackValue) -> UserFeedback:
        """Record or update user feedback reaction on a post."""
        feedback = UserFeedback(user_id=user_id, post_id=post_id, value=value)
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def category_affinity(self, user_id: int) -> dict[str, float]:
        """Aggregate interested counts grouped by post category."""
        result = await self.session.execute(
            select(Post.category, func.count(UserFeedback.id))
            .join(UserFeedback, UserFeedback.post_id == Post.id)
            .where(UserFeedback.user_id == user_id, UserFeedback.value == FeedbackValue.interested)
            .group_by(Post.category)
        )
        return {category or "Uncategorized": float(count) for category, count in result.all()}

    async def channel_affinity(self, user_id: int) -> dict[int, float]:
        """Aggregate interested counts grouped by channel id."""
        result = await self.session.execute(
            select(Post.channel_id, func.count(UserFeedback.id))
            .join(UserFeedback, UserFeedback.post_id == Post.id)
            .where(UserFeedback.user_id == user_id, UserFeedback.value == FeedbackValue.interested)
            .group_by(Post.channel_id)
        )
        return {channel_id: float(count) for channel_id, count in result.all()}

    async def post_reaction_stats(
        self,
        channel_id: int | None = None,
        limit: int = 50,
    ) -> list[PostReactionStat]:
        """Return post reaction metrics filtered by channel or overall."""
        statement = (
            select(
                Post.id,
                Post.channel_id,
                Channel.title,
                Post.telegram_message_id,
                func.count(UserFeedback.id).label("total_reactions"),
                func.sum((UserFeedback.value == FeedbackValue.interested).cast(Integer)).label(
                    "interested_reactions"
                ),
                func.sum((UserFeedback.value == FeedbackValue.not_interested).cast(Integer)).label(
                    "not_interested_reactions"
                ),
            )
            .join(UserFeedback, UserFeedback.post_id == Post.id)
            .join(Channel, Channel.id == Post.channel_id)
            .group_by(Post.id, Post.channel_id, Channel.title, Post.telegram_message_id)
            .order_by(func.count(UserFeedback.id).desc(), Post.created_at.desc())
            .limit(limit)
        )
        if channel_id is not None:
            statement = statement.where(Post.channel_id == channel_id)

        rows = (await self.session.execute(statement)).all()
        stats: list[PostReactionStat] = []
        for row in rows:
            total = int(row.total_reactions or 0)
            interested = int(row.interested_reactions or 0)
            not_interested = int(row.not_interested_reactions or 0)
            stats.append(
                PostReactionStat(
                    post_id=int(row.id),
                    channel_id=int(row.channel_id),
                    channel_title=row.title,
                    telegram_message_id=int(row.telegram_message_id),
                    total_reactions=total,
                    interested_reactions=interested,
                    not_interested_reactions=not_interested,
                    interested_ratio=(interested / total) if total else 0.0,
                )
            )
        return stats
