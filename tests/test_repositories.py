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

