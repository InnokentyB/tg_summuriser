from tg_summariser.models import PostStatus
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


class FakeAIPipeline:
    async def process_post(self, text: str):
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

