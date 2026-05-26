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
    queue = ChannelOnboardingQueue(bot=FakeBot(), ingestion_service=FakeIngestionService())

    first = await queue.enqueue(channel_id=1, telegram_user_id=100)
    second = await queue.enqueue(channel_id=1, telegram_user_id=100)

    assert first is True
    assert second is False
