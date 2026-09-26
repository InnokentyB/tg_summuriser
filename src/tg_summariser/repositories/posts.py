from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.config import settings
from tg_summariser.models import Channel, DigestItem, Post, PostStatus
from tg_summariser.repositories.utils import (
    article_key as _article_key_func,
    canonical_url as _canonical_url_func,
    external_urls as _external_urls_func,
    normalize_telegram_chat_id,
    registrable_domain as _registrable_domain_func,
    vendor_key as _vendor_key_func,
)
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.prefilter import LocalPrefilter


class PostRepository:
    """Persistence and candidate selection operations for ingested posts."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def create_post(
        self,
        channel_id: int,
        telegram_message_id: int,
        raw_text: str,
        normalized_text: str,
        original_link: str | None,
        source_published_at: datetime | None = None,
    ) -> tuple[Post, bool]:
        """Create a post idempotently, matching by channel/message_id or original_link."""
        result = await self.session.execute(
            select(Post).where(
                Post.channel_id == channel_id, Post.telegram_message_id == telegram_message_id
            ).order_by(Post.id.asc())
        )
        existing = result.scalars().first()
        if existing:
            if source_published_at and existing.source_published_at is None:
                existing.source_published_at = source_published_at
            return existing, False

        if original_link:
            result = await self.session.execute(
                select(Post).where(Post.original_link == original_link).order_by(Post.id.asc())
            )
            existing = result.scalars().first()
            if existing:
                if source_published_at and existing.source_published_at is None:
                    existing.source_published_at = source_published_at
                return existing, False

        post = Post(
            channel_id=channel_id,
            telegram_message_id=telegram_message_id,
            raw_text=raw_text,
            normalized_text=normalized_text,
            original_link=original_link,
            source_published_at=source_published_at,
        )
        self.session.add(post)
        await self.session.flush()
        return post, True

    async def get(self, post_id: int) -> Post | None:
        """Retrieve post by primary key id with joined channel relation."""
        result = await self.session.execute(
            select(Post).options(selectinload(Post.channel)).where(Post.id == post_id)
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_source(self, telegram_chat_id: int, telegram_message_id: int) -> Post | None:
        """Find post by source Telegram chat and message id."""
        normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .join(Channel)
            .where(
                Channel.telegram_chat_id == normalized_chat_id,
                Post.telegram_message_id == telegram_message_id,
            )
            .order_by(Post.id.asc())
        )
        return result.scalars().first()

    async def pending_posts(
        self,
        limit: int | None = None,
        channel_id: int | None = None,
    ) -> list[Post]:
        """Fetch posts pending AI processing and not assigned to an active batch."""
        stmt = (
            select(Post)
            .where(Post.status == PostStatus.pending, Post.ai_batch_job_id.is_(None))
            .order_by(Post.created_at.desc())
        )
        if channel_id is not None:
            stmt = stmt.where(Post.channel_id == channel_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def hide_stale_pending(self, max_age_days: int, channel_id: int | None = None) -> int:
        """Mark old pending posts as hidden before AI processing."""
        freshness_cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        stmt = update(Post).where(
            Post.status == PostStatus.pending,
            or_(
                Post.source_published_at.is_(None),
                Post.source_published_at < freshness_cutoff,
            ),
        )
        if channel_id is not None:
            stmt = stmt.where(Post.channel_id == channel_id)
        result = await self.session.execute(
            stmt.values(
                status=PostStatus.hidden,
                explanation="Скрыто до AI: публикация старше окна дайджеста или дата неизвестна.",
            )
        )
        return result.rowcount or 0

    async def top_candidates(
        self,
        limit: int | None = None,
        categories: list[str] | None = None,
    ) -> list[Post]:
        """Select top scored and diverse candidate posts across all channels."""
        freshness_cutoff = datetime.utcnow() - timedelta(days=settings.digest_max_post_age_days)
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
                Post.is_promotional.is_(False),
                or_(
                    Post.importance_score >= settings.digest_min_importance_score,
                    Post.relevance_score >= settings.digest_min_relevance_score,
                ),
                Post.source_published_at >= freshness_cutoff,
            )
        )
        if categories:
            stmt = stmt.where(Post.category.in_(categories))
        stmt = stmt.order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit * 10)
        result = await self.session.execute(stmt)
        sent_keys = await self._sent_source_keys()
        sent_posts = await self._sent_posts()
        recent_article_keys = await self._recent_digest_article_keys(days=7)
        return self._dedupe_posts(
            list(result.scalars()),
            limit,
            excluded_keys=sent_keys,
            excluded_posts=sent_posts,
            recent_article_keys=recent_article_keys,
            enforce_vendor_diversity=True,
        )

    async def top_candidates_for_channel(
        self,
        channel_id: int,
        limit: int | None = None,
        categories: list[str] | None = None,
    ) -> list[Post]:
        """Select top candidate posts specifically for one channel."""
        freshness_cutoff = datetime.utcnow() - timedelta(days=settings.digest_max_post_age_days)
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.channel_id == channel_id,
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
                Post.is_promotional.is_(False),
                or_(
                    Post.importance_score >= settings.digest_min_importance_score,
                    Post.relevance_score >= settings.digest_min_relevance_score,
                ),
                Post.source_published_at >= freshness_cutoff,
            )
        )
        if categories:
            stmt = stmt.where(Post.category.in_(categories))
        stmt = stmt.order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit * 10)
        result = await self.session.execute(stmt)
        sent_keys = await self._sent_source_keys(channel_id=channel_id)
        sent_posts = await self._sent_posts(channel_id=channel_id)
        recent_article_keys = await self._recent_digest_article_keys(days=7)
        return self._dedupe_posts(
            list(result.scalars()),
            limit,
            excluded_keys=sent_keys,
            excluded_posts=sent_posts,
            recent_article_keys=recent_article_keys,
            enforce_vendor_diversity=False,
        )

    async def hidden_posts(self, limit: int = 10) -> list[Post]:
        """Fetch recently hidden posts with channel metadata."""
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.status == PostStatus.hidden)
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def hidden_posts_for_channel(self, channel_id: int, limit: int = 3) -> list[Post]:
        """Fetch highest scoring hidden posts for diagnostics in a channel."""
        result = await self.session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(Post.channel_id == channel_id, Post.status == PostStatus.hidden)
            .order_by(Post.relevance_score.desc(), Post.importance_score.desc(), Post.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def channel_status_counts(self, channel_id: int) -> dict[PostStatus, int]:
        """Count posts per status enum for a specific channel."""
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
        """Count total posts from this channel that were ever sent in digests."""
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
        """Search posts by text query, category, and channel title."""
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

    async def _recent_digest_article_keys(self, days: int) -> set[str]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(Post)
            .join(DigestItem, DigestItem.post_id == Post.id)
            .where(DigestItem.created_at >= cutoff)
            .order_by(DigestItem.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return {
            art_key
            for post in result.scalars()
            if (art_key := self._article_key(post)) is not None
        }

    def _dedupe_posts(
        self,
        posts: Iterable[Post],
        limit: int | None,
        excluded_keys: set[str] | None = None,
        excluded_posts: list[Post] | None = None,
        recent_article_keys: set[str] | None = None,
        enforce_vendor_diversity: bool = False,
    ) -> list[Post]:
        unique_posts: list[Post] = []
        seen_keys: set[str] = set(excluded_keys or set())
        seen_article_keys: set[str] = set(recent_article_keys or set())
        seen_vendor_keys: set[str] = set()
        deduplicator = Deduplicator()
        prefilter = LocalPrefilter()
        reference_posts = list(excluded_posts or [])
        for post in posts:
            if prefilter.is_promotional(post):
                continue
            key = self._source_key(post)
            if key in seen_keys:
                continue
            art_key = self._article_key(post)
            if art_key and art_key in seen_article_keys:
                continue
            vend_key = self._vendor_key(post)
            if enforce_vendor_diversity and vend_key and vend_key in seen_vendor_keys:
                continue
            if deduplicator.find_duplicate(post, reference_posts):
                continue
            seen_keys.add(key)
            if art_key:
                seen_article_keys.add(art_key)
            if vend_key:
                seen_vendor_keys.add(vend_key)
            unique_posts.append(post)
            reference_posts.append(post)
            if limit is not None and len(unique_posts) >= limit:
                break
        return unique_posts

    @staticmethod
    def _source_key(post: Post) -> str:
        if post.original_link:
            return f"link:{post.original_link}"
        return f"channel:{post.channel_id}:message:{post.telegram_message_id}"

    @classmethod
    def _article_key(cls, post: Post) -> str | None:
        return _article_key_func(post)

    @classmethod
    def _vendor_key(cls, post: Post) -> str | None:
        return _vendor_key_func(post)

    @classmethod
    def _external_urls(cls, post: Post) -> list[str]:
        return _external_urls_func(post)

    @staticmethod
    def _canonical_url(raw_url: str) -> str | None:
        return _canonical_url_func(raw_url)

    @staticmethod
    def _registrable_domain(host: str) -> str:
        return _registrable_domain_func(host)
