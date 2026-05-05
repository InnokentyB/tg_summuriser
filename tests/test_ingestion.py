from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.telegram_client import TelegramChannelPost


class FakeTelegramClient:
    def __init__(self, posts: list[TelegramChannelPost]) -> None:
        self.posts = posts

    def is_connected(self) -> bool:
        return True

    async def iter_recent_channel_posts(self, channel_ref, limit: int = 15):
        return self.posts[:limit]


async def test_ingestion_syncs_new_posts_only(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=321,
        title="Business Brain",
        telegram_username="businessbrain",
        is_private=False,
    )

    posts = [
        TelegramChannelPost(
            channel_chat_id=321,
            channel_title="Business Brain",
            channel_username="businessbrain",
            message_id=1,
            text="First business post",
            link="https://t.me/businessbrain/1",
        ),
        TelegramChannelPost(
            channel_chat_id=321,
            channel_title="Business Brain",
            channel_username="businessbrain",
            message_id=2,
            text="Second business post",
            link="https://t.me/businessbrain/2",
        ),
    ]

    service = IngestionService(FakeTelegramClient(posts))
    first_sync = await service.sync_channels(db_session)
    second_sync = await service.sync_channels(db_session)
    stored_posts = await PostRepository(db_session).pending_posts()

    assert channel.id is not None
    assert first_sync == 2
    assert second_sync == 0
    assert len(stored_posts) == 2

