from __future__ import annotations

import json
from types import SimpleNamespace

from tg_summariser.models import PostStatus
from tg_summariser.schemas import ProcessedPost
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.openai_batch import OpenAIBatchService
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


def test_processed_post_truncates_long_language_and_category() -> None:
    """Verify ProcessedPost automatically clamps language to 16 and category to 255 chars."""
    post = ProcessedPost(
        language="эмодзи, без вербального текста",  # 30 chars
        summary="Some summary",
        why_important="Important",
        category="A" * 300,
        importance_score=0.5,
        relevance_score=0.5,
        explanation="Explanation",
    )
    assert len(post.language) <= 16
    assert post.language == "эмодзи, без верб"
    assert len(post.category) <= 255
    assert post.category == "A" * 255


def test_ai_pipeline_parse_results_truncates_language() -> None:
    """Verify AIPipeline clamps parsed language to 16 chars."""
    pipeline = AIPipeline()
    raw_json = json.dumps(
        {
            "results": [
                {
                    "id": 1,
                    "language": "эмодзи, без вербального текста",
                    "summary": "Summary",
                    "why_important": "Why",
                    "category": "C" * 300,
                    "importance_score": 0.5,
                    "relevance_score": 0.5,
                    "explanation": "Exp",
                }
            ]
        }
    )
    results = pipeline.parse_results(raw_json, [(1, "raw post text")])
    assert 1 in results
    assert len(results[1].language) <= 16
    assert results[1].language == "эмодзи, без верб"
    assert len(results[1].category) <= 255


async def test_post_processor_safely_truncates_language(db_session) -> None:
    """Verify PostProcessor truncates post.language to 16 chars before DB flush."""
    user = await UserRepository(db_session).get_or_create(telegram_id=999, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9099,
        title="Truncation Test",
        telegram_username="truncation_test",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=201,
        raw_text="Пост про AI агентов и LLM модели для тестирования длинного языка.",
        normalized_text="Пост про AI агентов и LLM модели для тестирования длинного языка.",
        original_link="https://t.me/truncation_test/201",
    )

    class LongLangAIPipeline:
        async def process_posts(self, posts):
            class LongResult:
                language = "эмодзи, без вербального текста"
                summary = "Short summary"
                why_important = "Reason"
                category = "C" * 300
                importance_score = 0.5
                relevance_score = 0.5
                explanation = "Explanation"

            return {p[0]: LongResult() for p in posts}

    class DummyDeduplicator:
        def find_duplicate(self, post, existing_posts):
            return None

    class DummyScorer:
        def score(self, post, category_affinity, channel_affinity):
            return 0.8, PostStatus.processed, "Scored"

    processor = PostProcessor(LongLangAIPipeline(), DummyDeduplicator(), DummyScorer())  # type: ignore[arg-type]
    processed_count = await processor.process_pending(db_session, user.id)

    assert processed_count == 1
    assert len(post.language) <= 16
    assert post.language == "эмодзи, без верб"
    assert len(post.category) <= 255
    await db_session.flush()


async def test_openai_batch_safely_truncates_language(db_session) -> None:
    """Verify OpenAIBatchService truncates post.language to 16 chars."""
    await UserRepository(db_session).get_or_create(telegram_id=998, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9098,
        title="Batch Truncation Test",
        telegram_username="batch_truncation_test",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=202,
        raw_text="Batch test content",
        normalized_text="Batch test content",
        original_link="https://t.me/batch_truncation_test/202",
    )

    # Even if an untruncated object was passed directly to _apply_ai_result
    fake_result = SimpleNamespace(
        language="эмодзи, без вербального текста",
        summary="Summary",
        why_important="Why",
        category="Cat",
        importance_score=0.5,
        relevance_score=0.5,
        explanation="Exp",
        is_promotional=False,
    )
    OpenAIBatchService._apply_ai_result(post, fake_result)  # type: ignore[arg-type]
    assert len(post.language) <= 16
    assert post.language == "эмодзи, без верб"
