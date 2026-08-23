import json
from types import SimpleNamespace

from sqlalchemy import select

from tg_summariser.config import settings
from tg_summariser.models import AIBatchJob, PostStatus
from tg_summariser.services.openai_batch import OpenAIBatchService
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


class FakeFiles:
    def __init__(self) -> None:
        self.uploaded = ""
        self.output = ""

    async def create(self, *, file, purpose):
        self.uploaded = file[1].decode()
        return SimpleNamespace(id="file-input")

    async def content(self, file_id):
        return SimpleNamespace(text=self.output)


class FakeBatches:
    def __init__(self) -> None:
        self.status = "validating"
        self.created = 0

    async def create(self, **kwargs):
        self.created += 1
        return SimpleNamespace(id="batch-1", status="validating")

    async def retrieve(self, batch_id):
        return SimpleNamespace(
            id=batch_id,
            status=self.status,
            output_file_id="file-output" if self.status == "completed" else None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.files = FakeFiles()
        self.batches = FakeBatches()


async def _create_posts(db_session, count: int):
    user = await UserRepository(db_session).get_or_create(telegram_id=123, username="owner")
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9005,
        title="AI Batch News",
        telegram_username="aibatchnews",
        is_private=False,
    )
    posts = []
    for message_id in range(1, count + 1):
        post, _ = await PostRepository(db_session).create_post(
            channel_id=channel.id,
            telegram_message_id=message_id,
            raw_text=f"AI agents product update number {message_id} " * 10,
            normalized_text=f"ai agents product update number {message_id}",
            original_link=f"https://t.me/aibatchnews/{message_id}",
        )
        posts.append(post)
    return user, posts


async def test_batch_submission_groups_posts_and_reserves_them(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_batch_enabled", True)
    monkeypatch.setattr(settings, "ai_batch_size", 5)
    user, posts = await _create_posts(db_session, 6)
    client = FakeClient()

    submitted = await OpenAIBatchService(client).submit_pending(db_session, user.id)
    lines = client.files.uploaded.strip().splitlines()

    assert submitted == 6
    assert client.batches.created == 1
    assert len(lines) == 2
    assert all(json.loads(line)["url"] == "/v1/responses" for line in lines)
    assert all(post.ai_batch_job_id is not None for post in posts)
    assert await PostRepository(db_session).pending_posts() == []


async def test_completed_batch_applies_results(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_batch_enabled", True)
    user, posts = await _create_posts(db_session, 2)
    client = FakeClient()
    service = OpenAIBatchService(client)
    await service.submit_pending(db_session, user.id)

    results = [
        {
            "id": post.id,
            "language": "ru",
            "summary": f"Саммари {post.id}",
            "why_important": "Важно",
            "category": "AI",
            "importance_score": 0.8,
            "relevance_score": 0.7,
            "explanation": "Пакетная оценка",
            "is_promotional": False,
        }
        for post in posts
    ]
    body = {
        "output": [
            {"content": [{"type": "output_text", "text": json.dumps({"results": results})}]}
        ]
    }
    client.files.output = json.dumps(
        {"custom_id": "posts", "response": {"status_code": 200, "body": body}}
    )
    client.batches.status = "completed"

    applied = await service.collect_completed(db_session, user.id)
    job = (await db_session.execute(select(AIBatchJob))).scalar_one()

    assert applied == 2
    assert job.status == "completed"
    assert posts[0].status == PostStatus.processed
    assert all(post.status != PostStatus.pending for post in posts)
    assert posts[0].summary == f"Саммари {posts[0].id}"


async def test_failed_batch_releases_posts_for_fast_processing(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_batch_enabled", True)
    user, posts = await _create_posts(db_session, 1)
    client = FakeClient()
    service = OpenAIBatchService(client)
    await service.submit_pending(db_session, user.id)
    client.batches.status = "failed"

    applied = await service.collect_completed(db_session, user.id)
    pending = await PostRepository(db_session).pending_posts()

    assert applied == 0
    assert posts[0].ai_batch_job_id is None
    assert pending == posts
