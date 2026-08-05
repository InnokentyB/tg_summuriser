import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from tg_summariser.services.channel_onboarding_queue import ChannelOnboardingQueue


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, telegram_id: int, text: str, **kwargs) -> None:
        self.messages.append((telegram_id, text))


class FakeIngestionService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def sync_channel(self, session, channel) -> int:
        self.calls.append((channel.id, channel.telegram_chat_id))
        return 0


async def test_onboarding_queue_dedupes_same_channel_enqueues() -> None:
    queue = ChannelOnboardingQueue(
        bot=FakeBot(),
        ingestion_service=FakeIngestionService(),
        persist_tasks=False,
    )

    first = await queue.enqueue(channel_id=1, telegram_user_id=100)
    second = await queue.enqueue(channel_id=1, telegram_user_id=100)

    assert first is True
    assert second is False


async def test_onboarding_queue_reschedules_existing_pending_db_job(monkeypatch) -> None:
    class FakeJobRepository:
        def __init__(self, session) -> None:
            self.session = session

        async def enqueue(self, channel_id: int, telegram_user_id: int):
            return object(), False

    @asynccontextmanager
    async def fake_session_scope():
        yield object()

    import tg_summariser.services.channel_onboarding_queue as queue_module

    monkeypatch.setattr(queue_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(queue_module, "ChannelOnboardingJobRepository", FakeJobRepository)
    queue = ChannelOnboardingQueue(bot=FakeBot(), ingestion_service=FakeIngestionService())
    queue.worker_task = asyncio.create_task(asyncio.sleep(60))

    try:
        queued = await queue.enqueue(channel_id=1, telegram_user_id=100)
    finally:
        queue.worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await queue.worker_task

    assert queued is True
    assert 1 in queue.pending_channel_ids
    assert queue.queue.qsize() == 1
