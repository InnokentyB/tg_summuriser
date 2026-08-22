from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.config import settings
from tg_summariser.services.repositories import ChannelRepository, PostRepository


@dataclass(slots=True)
class TGArticleCandidate:
    article_id: int
    title: str
    text: str
    summary: str | None = None
    source_name: str | None = None
    original_link: str | None = None
    canonical_url: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None


class TGArticlesSource(Protocol):
    async def fetch_recent_articles(
        self,
        *,
        days: int,
        limit: int,
        min_text_length: int,
    ) -> list[TGArticleCandidate]:
        pass


class AsyncpgTGArticlesSource:
    def __init__(self, database_url: str) -> None:
        self.database_url = self._normalize_database_url(database_url)

    async def fetch_recent_articles(
        self,
        *,
        days: int,
        limit: int,
        min_text_length: int,
    ) -> list[TGArticleCandidate]:
        import asyncpg

        conn = await asyncpg.connect(self.database_url)
        try:
            rows = await conn.fetch(
                """
                SELECT
                    a.id AS article_id,
                    a.title,
                    a.summary,
                    a.text,
                    a.original_link,
                    a.canonical_url,
                    a.language,
                    a.published_at,
                    a.created_at,
                    COALESCE(s.name, a.source) AS source_name
                FROM articles a
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE COALESCE(a.published_at, a.created_at) >= NOW() - ($1::int * INTERVAL '1 day')
                  AND COALESCE(length(a.text), 0) >= $2
                  AND COALESCE(a.title, '') <> ''
                ORDER BY COALESCE(a.published_at, a.created_at) DESC, a.id DESC
                LIMIT $3
                """,
                days,
                min_text_length,
                limit,
            )
        finally:
            await conn.close()

        return [
            TGArticleCandidate(
                article_id=int(row["article_id"]),
                title=row["title"] or f"Article {row['article_id']}",
                summary=row["summary"],
                text=row["text"] or "",
                source_name=row["source_name"],
                original_link=row["original_link"],
                canonical_url=row["canonical_url"],
                language=row["language"],
                published_at=row["published_at"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        if database_url.startswith("postgresql+asyncpg://"):
            return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        return database_url


class TGArticlesImportService:
    def __init__(
        self,
        source: TGArticlesSource | None = None,
        *,
        source_chat_id: int | None = None,
    ) -> None:
        self.source = source
        self.source_chat_id = source_chat_id or settings.tgarticles_source_chat_id

    @classmethod
    def from_settings(cls) -> "TGArticlesImportService | None":
        if not settings.tgarticles_import_enabled or not settings.tgarticles_database_url:
            return None
        return cls(AsyncpgTGArticlesSource(settings.tgarticles_database_url))

    async def import_recent(
        self,
        session: AsyncSession,
        *,
        days: int | None = None,
        limit: int | None = None,
        min_text_length: int | None = None,
    ) -> int:
        if not self.source:
            return 0

        articles = await self.source.fetch_recent_articles(
            days=days or settings.tgarticles_import_days,
            limit=limit or settings.tgarticles_import_limit,
            min_text_length=min_text_length or settings.tgarticles_min_text_length,
        )
        if not articles:
            return 0

        channel = await ChannelRepository(session).upsert_channel(
            telegram_chat_id=self.source_chat_id,
            title="TGArticles Library",
            telegram_username=None,
            is_private=True,
            source_kind="tg_articles",
        )
        post_repo = PostRepository(session)

        imported = 0
        for article in articles:
            normalized = " ".join(self._render_article(article).split())
            _, created = await post_repo.create_post(
                channel_id=channel.id,
                telegram_message_id=article.article_id,
                raw_text=self._render_article(article),
                normalized_text=normalized,
                original_link=article.canonical_url or article.original_link,
                source_published_at=self._naive_utc(article.published_at or article.created_at),
            )
            if created:
                imported += 1
        return imported

    def _render_article(self, article: TGArticleCandidate) -> str:
        published = article.published_at or article.created_at
        parts = [
            f"Title: {article.title}",
            f"Source: {article.source_name or 'TGArticles'}",
        ]
        if article.language:
            parts.append(f"Language: {article.language}")
        if published:
            parts.append(f"Published: {self._format_datetime(published)}")
        if article.summary:
            parts.append(f"Summary: {article.summary}")
        link = article.canonical_url or article.original_link
        if link:
            parts.append(f"Link: {link}")
        parts.append("")
        parts.append(article.text)
        return "\n".join(parts)

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @staticmethod
    def _naive_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
