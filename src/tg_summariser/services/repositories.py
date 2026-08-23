from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import Integer, Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.config import settings
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
from tg_summariser.services.prefilter import LocalPrefilter

_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_PARAMS = {"fbclid", "gclid", "yclid"}
_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_KNOWN_VENDOR_PATTERNS = (
    ("visure", re.compile(r"\bvisure(?:\s+solutions)?\b", re.IGNORECASE)),
    ("netflix", re.compile(r"\bnetflix\b", re.IGNORECASE)),
    ("gitlab", re.compile(r"\bgitlab\b", re.IGNORECASE)),
    ("aws", re.compile(r"\baws\b|\bamazon\s+web\s+services\b", re.IGNORECASE)),
    ("langchain", re.compile(r"\blangchain\b", re.IGNORECASE)),
    ("openai", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("anthropic", re.compile(r"\banthropic\b|\bclaude\b", re.IGNORECASE)),
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
        result = await self.session.execute(select(Channel).order_by(Channel.title))
        return list(result.scalars())

    async def list_telegram_channels(self) -> list[Channel]:
        result = await self.session.execute(
            select(Channel)
            .where(Channel.is_active.is_(True), Channel.source_kind == "telegram_channel")
            .order_by(Channel.title)
        )
        return list(result.scalars())

    async def count_telegram_channels(self) -> int:
        result = await self.session.execute(
            select(func.count(Channel.id)).where(
                Channel.is_active.is_(True),
                Channel.source_kind == "telegram_channel",
            )
        )
        return int(result.scalar_one())

    async def mark_synced(self, channel_id: int, synced_at: datetime | None = None) -> None:
        await self.session.execute(
            update(Channel)
            .where(Channel.id == channel_id)
            .values(last_synced_at=synced_at or datetime.utcnow())
        )

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
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.channel_id == channel_id)
            .order_by(ChannelOnboardingJob.id.asc())
        )
        job = result.scalars().first()
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

    async def failed_jobs(self) -> list[ChannelOnboardingJob]:
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .options(selectinload(ChannelOnboardingJob.channel))
            .where(ChannelOnboardingJob.status == "failed")
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
        job.status = "failed"
        job.last_error = error[:1000]
        job.updated_at = datetime.utcnow()

    async def _get_by_channel_id(self, channel_id: int) -> ChannelOnboardingJob | None:
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.channel_id == channel_id)
            .order_by(ChannelOnboardingJob.id.asc())
        )
        return result.scalars().first()

    async def status_counts(self) -> dict[str, int]:
        result = await self.session.execute(
            select(ChannelOnboardingJob.status, func.count(ChannelOnboardingJob.id)).group_by(
                ChannelOnboardingJob.status
            )
        )
        return {status: int(count) for status, count in result.all()}

    async def recent_jobs(self, limit: int = 10) -> list[ChannelOnboardingJob]:
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .options(selectinload(ChannelOnboardingJob.channel))
            .order_by(ChannelOnboardingJob.updated_at.desc())
            .limit(limit)
        )
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
        source_published_at: datetime | None = None,
    ) -> tuple[Post, bool]:
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
            .order_by(Post.id.asc())
        )
        return result.scalars().first()

    async def pending_posts(self, limit: int | None = None) -> list[Post]:
        stmt = (
            select(Post)
            .where(Post.status == PostStatus.pending, Post.ai_batch_job_id.is_(None))
            .order_by(Post.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(
            stmt
        )
        return list(result.scalars())

    async def hide_stale_pending(self, max_age_days: int) -> int:
        freshness_cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        result = await self.session.execute(
            update(Post)
            .where(
                Post.status == PostStatus.pending,
                or_(
                    Post.source_published_at.is_(None),
                    Post.source_published_at < freshness_cutoff,
                ),
            )
            .values(
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
        freshness_cutoff = datetime.utcnow() - timedelta(days=settings.digest_max_post_age_days)
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
                Post.is_promotional.is_(False),
                Post.importance_score >= settings.digest_min_importance_score,
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
        freshness_cutoff = datetime.utcnow() - timedelta(days=settings.digest_max_post_age_days)
        stmt = (
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.channel_id == channel_id,
                Post.status == PostStatus.processed,
                Post.was_sent.is_(False),
                Post.is_promotional.is_(False),
                Post.importance_score >= settings.digest_min_importance_score,
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
            article_key
            for post in result.scalars()
            if (article_key := self._article_key(post)) is not None
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
            article_key = self._article_key(post)
            if article_key and article_key in seen_article_keys:
                continue
            vendor_key = self._vendor_key(post)
            if enforce_vendor_diversity and vendor_key and vendor_key in seen_vendor_keys:
                continue
            if deduplicator.find_duplicate(post, reference_posts):
                continue
            seen_keys.add(key)
            if article_key:
                seen_article_keys.add(article_key)
            if vendor_key:
                seen_vendor_keys.add(vendor_key)
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
        for url in cls._external_urls(post):
            return f"url:{url}"
        return None

    @classmethod
    def _vendor_key(cls, post: Post) -> str | None:
        text = " ".join(
            value
            for value in (post.summary, post.raw_text, post.normalized_text, post.why_important)
            if value
        )
        for vendor_key, pattern in _KNOWN_VENDOR_PATTERNS:
            if pattern.search(text):
                return f"vendor:{vendor_key}"

        for url in cls._external_urls(post):
            host = urlsplit(url).hostname or ""
            if host:
                return f"domain:{cls._registrable_domain(host)}"
        return None

    @classmethod
    def _external_urls(cls, post: Post) -> list[str]:
        values = [post.original_link, post.raw_text, post.normalized_text, post.summary, post.why_important]
        urls: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value:
                continue
            for raw_url in _URL_RE.findall(value):
                url = cls._canonical_url(raw_url)
                if not url:
                    continue
                host = urlsplit(url).hostname or ""
                if host in _TELEGRAM_HOSTS or host.endswith(".telegram.org"):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)
        return urls

    @staticmethod
    def _canonical_url(raw_url: str) -> str | None:
        cleaned = raw_url.rstrip(".,;:!?)]}»")
        parsed = urlsplit(cleaned)
        if not parsed.scheme or not parsed.netloc:
            return None

        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/") or "/"
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in _TRACKING_QUERY_PARAMS
                and not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme.lower(), host, path, query, ""))

    @staticmethod
    def _registrable_domain(host: str) -> str:
        parts = host.lower().removeprefix("www.").split(".")
        if len(parts) <= 2:
            return ".".join(parts)
        return ".".join(parts[-2:])


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
