from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.models import (
    Channel,
    Digest,
    DigestItem,
    FeedbackValue,
    Post,
    PostStatus,
    User,
    UserFeedback,
)


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
            channel.is_active = True
            return channel

        channel = Channel(
            telegram_chat_id=normalized_chat_id,
            title=title,
            telegram_username=telegram_username,
            is_private=is_private,
        )
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def list_channels(self) -> list[Channel]:
        result = await self.session.execute(select(Channel).order_by(Channel.title))
        return list(result.scalars())


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

    async def top_candidates(self, limit: int = 5) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.status == PostStatus.processed, Post.was_sent.is_(False))
            .order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
            .limit(limit * 5)
        )
        sent_keys = await self._sent_source_keys()
        return self._dedupe_posts(list(result.scalars()), limit, excluded_keys=sent_keys)

    async def top_candidates_for_channel(self, channel_id: int, limit: int = 5) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.channel_id == channel_id,
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
            )
            .order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
            .limit(limit * 5)
        )
        sent_keys = await self._sent_source_keys(channel_id=channel_id)
        return self._dedupe_posts(list(result.scalars()), limit, excluded_keys=sent_keys)

    async def hidden_posts(self, limit: int = 10) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.status == PostStatus.hidden)
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

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

    def _dedupe_posts(
        self,
        posts: Iterable[Post],
        limit: int,
        excluded_keys: set[str] | None = None,
    ) -> list[Post]:
        unique_posts: list[Post] = []
        seen_keys: set[str] = set(excluded_keys or set())
        for post in posts:
            key = self._source_key(post)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_posts.append(post)
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
