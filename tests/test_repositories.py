from sqlalchemy import inspect

from tg_summariser.models import PostStatus
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


async def test_create_post_is_idempotent(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=123,
        title="AI Digest",
        telegram_username="ai_digest",
        is_private=False,
    )

    repo = PostRepository(db_session)
    first, created_first = await repo.create_post(
        channel_id=channel.id,
        telegram_message_id=777,
        raw_text="hello world",
        normalized_text="hello world",
        original_link="https://t.me/ai_digest/777",
    )
    second, created_second = await repo.create_post(
        channel_id=channel.id,
        telegram_message_id=777,
        raw_text="hello world",
        normalized_text="hello world",
        original_link="https://t.me/ai_digest/777",
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id


async def test_user_get_or_create_updates_username(db_session) -> None:
    repo = UserRepository(db_session)
    user = await repo.get_or_create(telegram_id=42, username="old_name")
    same_user = await repo.get_or_create(telegram_id=42, username="new_name")

    assert user.id == same_user.id
    assert same_user.username == "new_name"


async def test_top_candidates_preloads_channel(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=456,
        title="Knowledge Management",
        telegram_username="km_channel",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=12,
        raw_text="A useful note about internal knowledge systems",
        normalized_text="A useful note about internal knowledge systems",
        original_link="https://t.me/km_channel/12",
    )
    post.status = PostStatus.processed
    post.relevance_score = 0.8
    post.importance_score = 0.7

    candidates = await PostRepository(db_session).top_candidates(limit=5)

    assert len(candidates) == 1
    assert "channel" not in inspect(candidates[0]).unloaded
    assert candidates[0].channel.title == "Knowledge Management"


async def test_top_candidates_for_channel_limits_results_to_single_source(db_session) -> None:
    first_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=111,
        title="AI One",
        telegram_username="ai_one",
        is_private=False,
    )
    second_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=222,
        title="AI Two",
        telegram_username="ai_two",
        is_private=False,
    )

    first_post, _ = await PostRepository(db_session).create_post(
        channel_id=first_channel.id,
        telegram_message_id=1,
        raw_text="First",
        normalized_text="First",
        original_link="https://t.me/ai_one/1",
    )
    first_post.status = PostStatus.processed
    first_post.relevance_score = 0.9

    second_post, _ = await PostRepository(db_session).create_post(
        channel_id=second_channel.id,
        telegram_message_id=1,
        raw_text="Second",
        normalized_text="Second",
        original_link="https://t.me/ai_two/1",
    )
    second_post.status = PostStatus.processed
    second_post.relevance_score = 0.95

    candidates = await PostRepository(db_session).top_candidates_for_channel(first_channel.id, limit=5)

    assert len(candidates) == 1
    assert candidates[0].id == first_post.id
