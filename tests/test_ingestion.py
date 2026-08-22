import asyncio
from datetime import datetime

import pytest

from tg_summariser.config import settings
from tg_summariser.services.ingestion import IngestionService
from tg_summariser.services.repositories import ChannelRepository, PostRepository
from tg_summariser.services.telegram_client import TelegramChannelPost


@pytest.fixture(autouse=True)
def fast_telegram_sync(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_sync_delay_seconds", 0)
    monkeypatch.setattr(settings, "telegram_channel_sync_timeout_seconds", 5)


class FakeTelegramClient:
    def __init__(
        self,
        posts: list[TelegramChannelPost],
        failures: dict[int | str, Exception] | None = None,
        delays: dict[int | str, float] | None = None,
    ) -> None:
        self.posts = posts
        self.failures = failures or {}
        self.delays = delays or {}
        self.read_marks: list[tuple[int | str, int]] = []
        self.channel_refs: list[int | str] = []

    def is_connected(self) -> bool:
        return True

    async def iter_recent_channel_posts(self, channel_ref, limit: int = 15):
        self.channel_refs.append(channel_ref)
        if channel_ref in self.delays:
            await asyncio.sleep(self.delays[channel_ref])
        if channel_ref in self.failures:
            raise self.failures[channel_ref]
        return self.posts[:limit]

    async def mark_channel_posts_read(self, channel_ref, max_message_id: int) -> None:
        self.read_marks.append((channel_ref, max_message_id))


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
            published_at=datetime(2026, 8, 20, 10, 0),
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
    first_post = next(post for post in stored_posts if post.telegram_message_id == 1)
    assert first_post.source_published_at == datetime(2026, 8, 20, 10, 0)
    assert service.tg_client.read_marks == [(321, 2), (321, 2)]


async def test_ingestion_can_sync_single_channel(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=654,
        title="Agents Weekly",
        telegram_username="agentsweekly",
        is_private=False,
    )

    posts = [
        TelegramChannelPost(
            channel_chat_id=654,
            channel_title="Agents Weekly",
            channel_username="agentsweekly",
            message_id=10,
            text="Agent systems are changing workflow automation",
            link="https://t.me/agentsweekly/10",
        )
    ]

    service = IngestionService(FakeTelegramClient(posts))
    synced = await service.sync_channel(db_session, channel)
    stored_posts = await PostRepository(db_session).pending_posts()

    assert synced == 1
    assert len(stored_posts) == 1
    assert service.tg_client.read_marks == [(654, 10)]


async def test_sync_channels_skips_non_telegram_sources(db_session) -> None:
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=910000001,
        title="TGArticles Library",
        telegram_username=None,
        is_private=True,
        source_kind="tg_articles",
    )
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=777,
        title="Real Telegram",
        telegram_username="realtelegram",
        is_private=False,
    )

    service = IngestionService(FakeTelegramClient([]))
    synced = await service.sync_channels(db_session)

    assert synced == 0
    assert service.tg_client.channel_refs == [777]


async def test_ingestion_falls_back_to_username_if_chat_id_lookup_fails(db_session) -> None:
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=778,
        title="Fallback Telegram",
        telegram_username="fallbacktelegram",
        is_private=False,
    )

    posts = [
        TelegramChannelPost(
            channel_chat_id=778,
            channel_title="Fallback Telegram",
            channel_username="fallbacktelegram",
            message_id=10,
            text="Fallback lookup works",
            link="https://t.me/fallbacktelegram/10",
        )
    ]
    client = FakeTelegramClient(posts, failures={778: RuntimeError("Could not find entity")})
    service = IngestionService(client)

    synced = await service.sync_channels(db_session)

    assert synced == 1
    assert client.channel_refs == [778, "fallbacktelegram"]
    assert client.read_marks == [("fallbacktelegram", 10)]


async def test_ingestion_skips_channel_on_telegram_flood_wait(db_session) -> None:
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=779,
        title="Flooded Telegram",
        telegram_username="floodedtelegram",
        is_private=False,
    )
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=780,
        title="Healthy Telegram",
        telegram_username="healthytelegram",
        is_private=False,
    )
    posts = [
        TelegramChannelPost(
            channel_chat_id=780,
            channel_title="Healthy Telegram",
            channel_username="healthytelegram",
            message_id=11,
            text="Healthy channel still syncs",
            link="https://t.me/healthytelegram/11",
        )
    ]
    client = FakeTelegramClient(
        posts,
        failures={779: RuntimeError("FloodWaitError: A wait of 50369 seconds is required")},
    )
    service = IngestionService(client)

    synced = await service.sync_channels(db_session)

    assert synced == 1
    assert client.channel_refs == [779, 780]


async def test_ingestion_syncs_all_channels_each_run(db_session) -> None:
    first_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=781,
        title="First Telegram",
        telegram_username="firsttelegram",
        is_private=False,
    )
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=782,
        title="Second Telegram",
        telegram_username="secondtelegram",
        is_private=False,
    )

    service = IngestionService(FakeTelegramClient([]))
    first_sync = await service.sync_channels(db_session)
    second_sync = await service.sync_channels(db_session)
    await db_session.refresh(first_channel)

    assert first_sync == 0
    assert second_sync == 0
    assert service.tg_client.channel_refs == [781, 782, 781, 782]
    assert first_channel.last_synced_at is not None


async def test_ingestion_skips_timed_out_channel_and_continues(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_channel_sync_timeout_seconds", 0.01)
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=783,
        title="A Slow Telegram",
        telegram_username="slowtelegram",
        is_private=False,
    )
    await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=784,
        title="B Fast Telegram",
        telegram_username="fasttelegram",
        is_private=False,
    )
    posts = [
        TelegramChannelPost(
            channel_chat_id=784,
            channel_title="Fast Telegram",
            channel_username="fasttelegram",
            message_id=12,
            text="Fast channel still syncs",
            link="https://t.me/fasttelegram/12",
        )
    ]
    client = FakeTelegramClient(posts, delays={783: 0.1})
    service = IngestionService(client)

    synced = await service.sync_channels(db_session)

    assert synced == 1
    assert client.channel_refs == [783, 784]
