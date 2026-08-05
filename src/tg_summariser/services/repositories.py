from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.models import (
    Channel,
    ChannelOnboardingJob,
    Digest,
    DigestItem,
    FeedbackValue,
    Post,
    PostStatus,
    User,
    UserCategoryPreference,
    UserFeedback,
)
from tg_summariser.services.dedup import Deduplicator


def normalize_telegram_chat_id(chat_id: int) -> int:
    raw = str(chat_id)
    if raw.startswith("-100"):
        return int(raw[4:])
    if raw.startswith("-"):
        return int(raw[1:])
    return chat_id


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, telegram_id: int, username: str | None = None) -> User:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user:
            if username and user.username != username:
                user.username = username
            return user

        user = User(telegram_id=telegram_id, username=username)
        self.session.add(user)
        await self.session.flush()
        return user


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_channel(
        self,
        telegram_chat_id: int,
        title: str,
        telegram_username: str | None,
        is_private: bool,
        source_kind: str = "telegram_channel",
    ) -> Channel:
        normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        channel = None

        if telegram_username:
            result = await self.session.execute(
                select(Channel).where(Channel.telegram_username == telegram_username)
            )
            channel = result.scalar_one_or_none()

        if channel is None:
            result = await self.session.execute(
                select(Channel).where(Channel.telegram_chat_id == normalized_chat_id)
            )
            channel = result.scalar_one_or_none()

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
        result = await self.session.execute(select(Channel).order_by(Channel.title))
        return list(result.scalars())

    async def list_telegram_channels(self) -> list[Channel]:
        result = await self.session.execute(
            select(Channel)
            .where(Channel.is_active.is_(True), Channel.source_kind == "telegram_channel")
            .order_by(Channel.title)
        )
        return list(result.scalars())

    async def get_by_id(self, channel_id: int) -> Channel | None:
        result = await self.session.execute(select(Channel).where(Channel.id == channel_id))
        return result.scalar_one_or_none()

    async def channels_without_posts(self) -> list[Channel]:
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


class ChannelOnboardingJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(self, channel_id: int, telegram_user_id: int) -> tuple[ChannelOnboardingJob, bool]:
        result = await self.session.execute(
            select(ChannelOnboardingJob).where(ChannelOnboardingJob.channel_id == channel_id)
        )
        job = result.scalar_one_or_none()
        if job:
            was_already_waiting = job.status in {"pending", "processing"}
            job.telegram_user_id = telegram_user_id
            job.status = "pending"
            job.updated_at = datetime.utcnow()
            job.completed_at = None
            job.last_error = None
            return job, not was_already_waiting

        job = ChannelOnboardingJob(
            channel_id=channel_id,
            telegram_user_id=telegram_user_id,
            status="pending",
        )
        self.session.add(job)
        await self.session.flush()
        return job, True

    async def recoverable_jobs(self) -> list[ChannelOnboardingJob]:
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.status.in_(["pending", "processing"]))
            .order_by(ChannelOnboardingJob.updated_at.asc())
        )
        return list(result.scalars())

    async def mark_processing(self, channel_id: int) -> None:
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "processing"
        job.attempts += 1
        job.updated_at = datetime.utcnow()

    async def mark_completed(self, channel_id: int) -> None:
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        job.last_error = None

    async def mark_failed(self, channel_id: int, error: str) -> None:
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "pending"
        job.last_error = error[:1000]
        job.updated_at = datetime.utcnow()

    async def _get_by_channel_id(self, channel_id: int) -> ChannelOnboardingJob | None:
        result = await self.session.execute(
            select(ChannelOnboardingJob).where(ChannelOnboardingJob.channel_id == channel_id)
        )
        return result.scalar_one_or_none()


class PostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_post(
        self,
        channel_id: int,
        telegram_message_id: int,
        raw_text: str,
        normalized_text: str,
        original_link: str | None,
    ) -> tuple[Post, bool]:
        result = await self.session.execute(
            select(Post).where(
                Post.channel_id == channel_id, Post.telegram_message_id == telegram_message_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing, False

        if original_link:
            result = await self.session.execute(select(Post).where(Post.original_link == original_link))
            existing = result.scalar_one_or_none()
            if existing:
                return existing, False

        post = Post(
            channel_id=channel_id,
            telegram_message_id=telegram_message_id,
            raw_text=raw_text,
            normalized_text=normalized_text,
            original_link=original_link,
        )
        self.session.add(post)
        await self.session.flush()
        return post, True

    async def get(self, post_id: int) -> Post | None:
        result = await self.session.execute(
            select(Post).options(selectinload(Post.channel)).where(Post.id == post_id)
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_source(self, telegram_chat_id: int, telegram_message_id: int) -> Post | None:
        normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .join(Channel)
            .where(
                Channel.telegram_chat_id == normalized_chat_id,
                Post.telegram_message_id == telegram_message_id,
            )
        )
        return result.scalar_one_or_none()

    async def pending_posts(self) -> list[Post]:
        result = await self.session.execute(
            select(Post).where(Post.status == PostStatus.pending).order_by(Post.created_at.desc())
        )
        return list(result.scalars())

    async def top_candidates(self, limit: int = 5, categories: list[str] | None = None) -> list[Post]:
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.status == PostStatus.processed, Post.was_sent.is_(False))
        )
        if categories:
            stmt = stmt.where(Post.category.in_(categories))
        stmt = stmt.order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
        result = await self.session.execute(stmt.limit(limit * 5))
        sent_keys = await self._sent_source_keys()
        sent_posts = await self._sent_posts()
        return self._dedupe_posts(
            list(result.scalars()),
            limit,
            excluded_keys=sent_keys,
            excluded_posts=sent_posts,
        )

    async def top_candidates_for_channel(
        self,
        channel_id: int,
        limit: int = 5,
        categories: list[str] | None = None,
    ) -> list[Post]:
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.channel_id == channel_id,
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
            )
        )
        if categories:
            stmt = stmt.where(Post.category.in_(categories))
        stmt = stmt.order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
        result = await self.session.execute(stmt.limit(limit * 5))
        sent_keys = await self._sent_source_keys(channel_id=channel_id)
        sent_posts = await self._sent_posts(channel_id=channel_id)
        return self._dedupe_posts(
            list(result.scalars()),
            limit,
            excluded_keys=sent_keys,
            excluded_posts=sent_posts,
        )

    async def hidden_posts(self, limit: int = 10) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.status == PostStatus.hidden)
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def hidden_posts_for_channel(self, channel_id: int, limit: int = 3) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.channel_id == channel_id, Post.status == PostStatus.hidden)
            .order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def channel_status_counts(self, channel_id: int) -> dict[PostStatus, int]:
        result = await self.session.execute(
            select(Post.status, func.count(Post.id))
            .where(Post.channel_id == channel_id)
            .group_by(Post.status)
        )
        counts = {status: 0 for status in PostStatus}
        for status, count in result.all():
            counts[PostStatus(status)] = int(count)
        return counts

    async def sent_count_for_channel(self, channel_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Post.id)).where(Post.channel_id == channel_id, Post.was_sent.is_(True))
        )
        return int(result.scalar_one())

    async def search(
        self,
        query: str,
        category: str | None = None,
        channel: str | None = None,
        limit: int = 10,
    ) -> list[Post]:
        stmt: Select[tuple[Post]] = select(Post).options(selectinload(Post.channel)).join(Channel)
        stmt = stmt.where(
            or_(
                Post.raw_text.ilike(f"%{query}%"),
                Post.summary.ilike(f"%{query}%"),
                Post.why_important.ilike(f"%{query}%"),
            )
        )
        if category:
            stmt = stmt.where(Post.category.ilike(f"%{category}%"))
        if channel:
            stmt = stmt.where(Channel.title.ilike(f"%{channel}%"))
        stmt = stmt.order_by(Post.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def _sent_source_keys(self, channel_id: int | None = None) -> set[str]:
        stmt = select(Post).where(Post.was_sent.is_(True))
        if channel_id is not None:
            stmt = stmt.where(Post.channel_id == channel_id)
        result = await self.session.execute(stmt)
        return {self._source_key(post) for post in result.scalars()}

    async def _sent_posts(self, channel_id: int | None = None, limit: int = 200) -> list[Post]:
        stmt = select(Post).where(Post.was_sent.is_(True)).order_by(Post.created_at.desc()).limit(limit)
        if channel_id is not None:
            stmt = stmt.where(Post.channel_id == channel_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    def _dedupe_posts(
        self,
        posts: Iterable[Post],
        limit: int,
        excluded_keys: set[str] | None = None,
        excluded_posts: list[Post] | None = None,
    ) -> list[Post]:
        unique_posts: list[Post] = []
        seen_keys: set[str] = set(excluded_keys or set())
        deduplicator = Deduplicator()
        reference_posts = list(excluded_posts or [])
        for post in posts:
            key = self._source_key(post)
            if key in seen_keys:
                continue
            if deduplicator.find_duplicate(post, reference_posts):
                continue
            seen_keys.add(key)
            unique_posts.append(post)
            reference_posts.append(post)
            if len(unique_posts) >= limit:
                break
        return unique_posts

    @staticmethod
    def _source_key(post: Post) -> str:
        if post.original_link:
            return f"link:{post.original_link}"
        return f"channel:{post.channel_id}:message:{post.telegram_message_id}"


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_feedback(self, user_id: int, post_id: int, value: FeedbackValue) -> UserFeedback:
        feedback = UserFeedback(user_id=user_id, post_id=post_id, value=value)
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def category_affinity(self, user_id: int) -> dict[str, float]:
        result = await self.session.execute(
            select(Post.category, func.count(UserFeedback.id))
            .join(UserFeedback, UserFeedback.post_id == Post.id)
            .where(UserFeedback.user_id == user_id, UserFeedback.value == FeedbackValue.interested)
            .group_by(Post.category)
        )
        return {category or "Uncategorized": float(count) for category, count in result.all()}

    async def channel_affinity(self, user_id: int) -> dict[int, float]:
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
    ) -> list["PostReactionStat"]:
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


@dataclass(slots=True)
class PostReactionStat:
    post_id: int
    channel_id: int
    channel_title: str
    telegram_message_id: int
    total_reactions: int
    interested_reactions: int
    not_interested_reactions: int
    interested_ratio: float


class UserCategoryPreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enabled_categories(self, user_id: int) -> list[str]:
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
        result = await self.session.execute(
            select(UserCategoryPreference)
            .where(UserCategoryPreference.user_id == user_id)
            .order_by(UserCategoryPreference.category)
        )
        return list(result.scalars())

    async def set_enabled(self, user_id: int, category: str, is_enabled: bool) -> UserCategoryPreference:
        normalized = category.strip()
        result = await self.session.execute(
            select(UserCategoryPreference).where(
                UserCategoryPreference.user_id == user_id,
                UserCategoryPreference.category == normalized,
            )
        )
        preference = result.scalar_one_or_none()
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
        preferences = await self.all_preferences(user_id)
        for preference in preferences:
            await self.session.delete(preference)
        return len(preferences)

    async def known_categories(self, limit: int = 100) -> list[str]:
        result = await self.session.execute(
            select(Post.category)
            .where(Post.category.is_not(None))
            .group_by(Post.category)
            .order_by(func.count(Post.id).desc(), Post.category.asc())
            .limit(limit)
        )
        return [row[0] for row in result.all() if row[0]]


class DigestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_digest(self, user_id: int, scheduled_for: datetime) -> Digest:
        digest = Digest(user_id=user_id, scheduled_for=scheduled_for)
        self.session.add(digest)
        await self.session.flush()
        return digest

    async def add_item(self, digest_id: int, post_id: int, rank: int) -> DigestItem:
        item = DigestItem(digest_id=digest_id, post_id=post_id, rank=rank)
        self.session.add(item)
        await self.session.flush()
        return item
