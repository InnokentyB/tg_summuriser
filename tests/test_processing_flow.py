from datetime import datetime, timedelta

from tg_summariser.models import PostStatus
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


class FakeAIPipeline:
    def __init__(self) -> None:
        self.calls = 0

    async def process_post(self, text: str):
        self.calls += 1

        class Result:
            language = "en"
            summary = "Short summary"
            why_important = "Important because it matches user priorities."
            category = "AI & Agents"
            importance_score = 0.8
            relevance_score = 0.6
            explanation = "Base AI explanation."

        return Result()


class FakeDeduplicator:
    def find_duplicate(self, post, existing_posts):
        return None


class FakeScorer:
    def score(self, post, category_affinity, channel_affinity):
        return 0.91, PostStatus.processed, "Scored for digest."


class HideEverythingPrefilter:
    def decide(self, post, *, channel_affinity):
        class Result:
            language = "ru"
            summary = "Hidden locally"
            why_important = "Not important"
            category = "Filtered"
            importance_score = 0.1
            relevance_score = 0.1
            explanation = "Hidden before AI."

        class Decision:
            should_call_ai = False
            ai_result = Result()
            forced_status = PostStatus.hidden
            explanation = "Hidden before AI."

        return Decision()


async def test_post_processor_enriches_pending_post(db_session) -> None:
    user = await UserRepository(db_session).get_or_create(telegram_id=1, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9001,
        title="AI Agents Daily",
        telegram_username="aiagentsdaily",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=101,
        raw_text="A long post about AI agents",
        normalized_text="A long post about AI agents",
        original_link="https://t.me/aiagentsdaily/101",
    )

    processor = PostProcessor(FakeAIPipeline(), FakeDeduplicator(), FakeScorer())
    processed = await processor.process_pending(db_session, user.id)

    assert processed == 1
    assert post.summary == "Short summary"
    assert post.why_important == "Important because it matches user priorities."
    assert post.category == "AI & Agents"
    assert post.relevance_score == 0.91
    assert post.status == PostStatus.processed
    assert post.explanation == "Scored for digest."


async def test_post_processor_can_hide_post_before_ai(db_session) -> None:
    user = await UserRepository(db_session).get_or_create(telegram_id=2, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9003,
        title="Promo Channel",
        telegram_username="promochannel",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=101,
        raw_text="Подписывайтесь на промо вебинар",
        normalized_text="Подписывайтесь на промо вебинар",
        original_link="https://t.me/promochannel/101",
    )

    ai_pipeline = FakeAIPipeline()
    processor = PostProcessor(
        ai_pipeline,
        FakeDeduplicator(),
        FakeScorer(),
        prefilter=HideEverythingPrefilter(),
    )
    processed = await processor.process_pending(db_session, user.id)

    assert processed == 1
    assert ai_pipeline.calls == 0
    assert post.status == PostStatus.hidden
    assert post.category == "Filtered"


async def test_post_processor_hides_stale_posts_before_ai(db_session) -> None:
    user = await UserRepository(db_session).get_or_create(telegram_id=3, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9004,
        title="Old News",
        telegram_username="oldnews",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=101,
        raw_text="An old post about AI agents",
        normalized_text="An old post about AI agents",
        original_link="https://t.me/oldnews/101",
        source_published_at=datetime.utcnow() - timedelta(days=10),
    )

    ai_pipeline = FakeAIPipeline()
    processor = PostProcessor(ai_pipeline, FakeDeduplicator(), FakeScorer())
    processed = await processor.process_pending(db_session, user.id)
    await db_session.refresh(post)

    assert processed == 0
    assert ai_pipeline.calls == 0
    assert post.status == PostStatus.hidden
    assert post.explanation == (
        "Скрыто до AI: публикация старше окна дайджеста или дата неизвестна."
    )
