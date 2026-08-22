import httpx
from openai import RateLimitError

from tg_summariser.config import settings
from tg_summariser.models import PostStatus
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.post_processor import PostProcessor
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


class FakeResponse:
    output_text = (
        '{"language":"ru","summary":"Саммари","why_important":"Важно",'
        '"category":"AI","importance_score":0.8,"relevance_score":0.7,'
        '"explanation":"AI оценка","is_promotional":true}'
    )


class FakeResponses:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.inputs: list[str] = []

    async def create(self, *, model: str, input: str):
        self.inputs.append(input)
        if self.error:
            raise self.error
        return FakeResponse()


class FakeClient:
    def __init__(self, responses: FakeResponses) -> None:
        self.responses = responses


class FakeDeduplicator:
    def find_duplicate(self, post, existing_posts):
        return None


class FakeScorer:
    def score(self, post, category_affinity, channel_affinity):
        return post.relevance_score, PostStatus.processed, post.explanation


def _quota_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request)
    return RateLimitError(
        message="You have no credits remaining.",
        response=response,
        body={"error": {"code": "credit_balance_exhausted"}},
    )


async def test_ai_pipeline_skips_api_for_short_posts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_min_text_length", 120)
    responses = FakeResponses()
    pipeline = AIPipeline()
    pipeline.client = FakeClient(responses)

    result = await pipeline.process_post("short ai note")

    assert result.summary == "short ai note"
    assert responses.inputs == []


async def test_ai_pipeline_trims_long_posts_before_api(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_min_text_length", 1)
    monkeypatch.setattr(settings, "ai_max_input_chars", 20)
    responses = FakeResponses()
    pipeline = AIPipeline()
    pipeline.client = FakeClient(responses)

    result = await pipeline.process_post("x" * 100)

    assert result.summary == "Саммари"
    assert result.is_promotional is True
    assert "x" * 21 not in responses.inputs[0]


async def test_ai_pipeline_falls_back_after_quota_exhaustion(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_min_text_length", 1)
    responses = FakeResponses(error=_quota_error())
    pipeline = AIPipeline()
    pipeline.client = FakeClient(responses)

    first = await pipeline.process_post("AI agents " * 20)
    second = await pipeline.process_post("AI agents " * 20)

    assert first.category == "AI & Agents"
    assert second.category == "AI & Agents"
    assert pipeline.api_disabled_reason == "insufficient_quota"
    assert len(responses.inputs) == 1


async def test_post_processor_limits_ai_work_per_run(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_processing_limit_per_run", 1)
    user = await UserRepository(db_session).get_or_create(telegram_id=1, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9002,
        title="AI Agents Daily",
        telegram_username="aiagentsdaily",
        is_private=False,
    )
    for message_id in (1, 2):
        await PostRepository(db_session).create_post(
            channel_id=channel.id,
            telegram_message_id=message_id,
            raw_text=f"AI post {message_id}",
            normalized_text=f"AI post {message_id}",
            original_link=f"https://t.me/aiagentsdaily/{message_id}",
        )

    processor = PostProcessor(AIPipeline(), FakeDeduplicator(), FakeScorer())
    processed = await processor.process_pending(db_session, user.id)
    pending = await PostRepository(db_session).pending_posts()

    assert processed == 1
    assert len(pending) == 1
