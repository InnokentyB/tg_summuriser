from __future__ import annotations

from datetime import datetime, timezone

from tg_summariser.models import PostStatus
from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.tgarticles_importer import TGArticleCandidate, TGArticlesImportService


class FakeTGArticlesSource:
    def __init__(self, articles: list[TGArticleCandidate]) -> None:
        self.articles = articles
        self.calls = []

    async def fetch_recent_articles(self, *, days: int, limit: int, min_text_length: int):
        self.calls.append(
            {
                "days": days,
                "limit": limit,
                "min_text_length": min_text_length,
            }
        )
        return self.articles


async def test_tgarticles_importer_creates_article_posts(db_session) -> None:
    source = FakeTGArticlesSource(
        [
            TGArticleCandidate(
                article_id=101,
                title="Good article about AI agents",
                summary="Short useful summary",
                text="AI agents and requirements engineering " * 80,
                source_name="Example Blog",
                canonical_url="https://example.com/ai-agents",
                language="en",
                published_at=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc),
            )
        ]
    )
    service = TGArticlesImportService(source, source_chat_id=910000099)

    imported = await service.import_recent(db_session, days=7, limit=10, min_text_length=500)

    channels = await ChannelRepository(db_session).list_channels()
    posts = await PostRepository(db_session).pending_posts()

    assert imported == 1
    assert source.calls == [{"days": 7, "limit": 10, "min_text_length": 500}]
    assert len(channels) == 1
    assert channels[0].title == "TGArticles Library"
    assert channels[0].source_kind == "tg_articles"
    assert len(posts) == 1
    assert posts[0].telegram_message_id == 101
    assert posts[0].status == PostStatus.pending
    assert posts[0].original_link == "https://example.com/ai-agents"
    assert "Good article about AI agents" in posts[0].raw_text
    assert "Short useful summary" in posts[0].raw_text


async def test_tgarticles_importer_is_idempotent_by_article_link(db_session) -> None:
    article = TGArticleCandidate(
        article_id=101,
        title="Good article about AI agents",
        text="AI agents and requirements engineering " * 80,
        source_name="Example Blog",
        canonical_url="https://example.com/ai-agents",
    )
    service = TGArticlesImportService(FakeTGArticlesSource([article]), source_chat_id=910000099)

    first_imported = await service.import_recent(db_session)
    second_imported = await service.import_recent(db_session)

    posts = await PostRepository(db_session).pending_posts()

    assert first_imported == 1
    assert second_imported == 0
    assert len(posts) == 1


async def test_tgarticles_importer_accepts_links_longer_than_500_characters(db_session) -> None:
    long_link = "https://example.com/redirect?token=" + "x" * 600
    article = TGArticleCandidate(
        article_id=102,
        title="Article behind a tracking redirect",
        text="Useful article text " * 80,
        original_link=long_link,
    )
    service = TGArticlesImportService(FakeTGArticlesSource([article]), source_chat_id=910000099)

    imported = await service.import_recent(db_session)
    posts = await PostRepository(db_session).pending_posts()

    assert imported == 1
    assert posts[0].original_link == long_link
