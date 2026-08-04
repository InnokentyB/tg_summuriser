from sqlalchemy import BigInteger, inspect

from tg_summariser.models import Channel, FeedbackValue, PostStatus, User
from tg_summariser.services.repositories import (
    ChannelRepository,
    FeedbackRepository,
    PostRepository,
    UserRepository,
    UserCategoryPreferenceRepository,
)


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


def test_telegram_identifiers_use_bigint_columns() -> None:
    assert isinstance(User.__table__.c.telegram_id.type, BigInteger)
    assert isinstance(Channel.__table__.c.telegram_chat_id.type, BigInteger)


async def test_upsert_channel_merges_forwarded_and_canonical_chat_ids(db_session) -> None:
    repo = ChannelRepository(db_session)
    first = await repo.upsert_channel(
        telegram_chat_id=-1001234567890,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    second = await repo.upsert_channel(
        telegram_chat_id=1234567890,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )

    assert first.id == second.id
    assert second.telegram_chat_id == 1234567890


async def test_upsert_channel_accepts_telegram_chat_id_above_int32(db_session) -> None:
    repo = ChannelRepository(db_session)

    channel = await repo.upsert_channel(
        telegram_chat_id=2_484_784_423,
        title="Large Telegram Channel",
        telegram_username=None,
        is_private=False,
    )
    same_channel = await repo.upsert_channel(
        telegram_chat_id=2_484_784_423,
        title="Large Telegram Channel Updated",
        telegram_username=None,
        is_private=False,
    )

    assert channel.id == same_channel.id
    assert same_channel.telegram_chat_id == 2_484_784_423
    assert same_channel.title == "Large Telegram Channel Updated"


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


async def test_create_post_dedupes_same_public_post_by_original_link(db_session) -> None:
    first_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=333,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    second_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=444,
        title="AI Product Mirror",
        telegram_username="ai_product_mirror",
        is_private=False,
    )

    repo = PostRepository(db_session)
    first_post, first_created = await repo.create_post(
        channel_id=first_channel.id,
        telegram_message_id=2169,
        raw_text="Codex import from Claude",
        normalized_text="Codex import from Claude",
        original_link="https://t.me/ai_product/2169",
    )
    second_post, second_created = await repo.create_post(
        channel_id=second_channel.id,
        telegram_message_id=9999,
        raw_text="Codex import from Claude repost",
        normalized_text="Codex import from Claude repost",
        original_link="https://t.me/ai_product/2169",
    )

    assert first_created is True
    assert second_created is False
    assert first_post.id == second_post.id


async def test_get_by_telegram_source_finds_post_for_normalized_chat_id(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=-1001286689600,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=2169,
        raw_text="Codex import from Claude",
        normalized_text="Codex import from Claude",
        original_link="https://t.me/ai_product/2169",
    )

    found = await PostRepository(db_session).get_by_telegram_source(1286689600, 2169)

    assert found is not None
    assert found.id == post.id


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


async def test_top_candidates_skip_duplicate_rows_of_same_public_post(db_session) -> None:
    first_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=555,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    second_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=556,
        title="AI Product Duplicate",
        telegram_username="ai_product_duplicate",
        is_private=False,
    )

    first_post, _ = await PostRepository(db_session).create_post(
        channel_id=first_channel.id,
        telegram_message_id=2169,
        raw_text="Codex import from Claude",
        normalized_text="Codex import from Claude",
        original_link="https://t.me/ai_product/2169",
    )
    first_post.status = PostStatus.processed
    first_post.relevance_score = 0.9

    duplicate = first_post.__class__(
        channel_id=second_channel.id,
        telegram_message_id=8888,
        raw_text="Codex import duplicate row",
        normalized_text="Codex import duplicate row",
        original_link="https://t.me/ai_product/2169",
        status=PostStatus.processed,
        relevance_score=0.85,
    )
    db_session.add(duplicate)
    await db_session.flush()

    candidates = await PostRepository(db_session).top_candidates(limit=5)

    assert len(candidates) == 1
    assert candidates[0].original_link == "https://t.me/ai_product/2169"


async def test_top_candidates_skip_unsent_duplicate_if_source_was_already_sent(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=777,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )

    sent_post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=2169,
        raw_text="Codex import from Claude",
        normalized_text="Codex import from Claude",
        original_link="https://t.me/ai_product/2169",
    )
    sent_post.status = PostStatus.processed
    sent_post.relevance_score = 0.9
    sent_post.was_sent = True

    duplicate = sent_post.__class__(
        channel_id=channel.id,
        telegram_message_id=9999,
        raw_text="Codex import duplicate row",
        normalized_text="Codex import duplicate row",
        original_link="https://t.me/ai_product/2169",
        status=PostStatus.processed,
        relevance_score=0.85,
        was_sent=False,
    )
    db_session.add(duplicate)
    await db_session.flush()

    candidates = await PostRepository(db_session).top_candidates(limit=5)

    assert candidates == []


async def test_top_candidates_skip_same_news_event_if_already_sent(db_session) -> None:
    first_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=778,
        title="AI Central",
        telegram_username="aioftheday",
        is_private=False,
    )
    second_channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=779,
        title="TGArticles Library",
        telegram_username="tgarticles",
        is_private=False,
        source_kind="tgarticles",
    )

    sent_post, _ = await PostRepository(db_session).create_post(
        channel_id=first_channel.id,
        telegram_message_id=4969,
        raw_text=(
            "OpenAI сообщает, что новая невыпущенная модель Astra решила десять "
            "математических задач, доказательства формализованы в Lean."
        ),
        normalized_text=(
            "OpenAI сообщает, что новая невыпущенная модель Astra решила десять "
            "математических задач, доказательства формализованы в Lean."
        ),
        original_link="https://t.me/aioftheday/4969",
    )
    sent_post.status = PostStatus.processed
    sent_post.relevance_score = 0.9
    sent_post.was_sent = True
    sent_post.summary = (
        "OpenAI сообщает, что модель Astra решила десять математических задач "
        "и формализовала доказательства в Lean."
    )

    duplicate, _ = await PostRepository(db_session).create_post(
        channel_id=second_channel.id,
        telegram_message_id=1,
        raw_text=(
            "OpenAI объявила, что её ещё не выпущенная модель решила десять открытых "
            "задач в математике; отмечена необходимость проверки заявлений."
        ),
        normalized_text=(
            "OpenAI объявила, что её ещё не выпущенная модель решила десять открытых "
            "задач в математике; отмечена необходимость проверки заявлений."
        ),
        original_link="https://borretti.me/article/mathematics-without-mathematicians",
    )
    duplicate.status = PostStatus.processed
    duplicate.relevance_score = 0.95
    duplicate.summary = (
        "OpenAI объявила, что невыпущенная модель решила десять открытых задач "
        "в математике и может изменить исследовательский процесс."
    )

    candidates = await PostRepository(db_session).top_candidates(limit=5)

    assert candidates == []


async def test_top_candidates_can_be_filtered_by_categories(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=888,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    ai_post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=1,
        raw_text="AI post",
        normalized_text="AI post",
        original_link="https://t.me/ai_product/1",
    )
    ai_post.status = PostStatus.processed
    ai_post.category = "AI tools"
    ai_post.relevance_score = 0.9

    business_post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=2,
        raw_text="Business post",
        normalized_text="Business post",
        original_link="https://t.me/ai_product/2",
    )
    business_post.status = PostStatus.processed
    business_post.category = "Business"
    business_post.relevance_score = 0.8

    candidates = await PostRepository(db_session).top_candidates(limit=5, categories=["AI tools"])

    assert len(candidates) == 1
    assert candidates[0].category == "AI tools"


async def test_post_reaction_stats_groups_by_post_and_channel(db_session) -> None:
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=333,
        title="Signals",
        telegram_username="signals",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=55,
        raw_text="Alpha",
        normalized_text="Alpha",
        original_link="https://t.me/signals/55",
    )
    first_user = await UserRepository(db_session).get_or_create(telegram_id=501, username="u1")
    second_user = await UserRepository(db_session).get_or_create(telegram_id=502, username="u2")

    feedback_repo = FeedbackRepository(db_session)
    await feedback_repo.add_feedback(first_user.id, post.id, FeedbackValue.interested)
    await feedback_repo.add_feedback(second_user.id, post.id, FeedbackValue.not_interested)

    stats = await feedback_repo.post_reaction_stats()

    assert len(stats) == 1
    assert stats[0].post_id == post.id
    assert stats[0].channel_id == channel.id
    assert stats[0].channel_title == "Signals"
    assert stats[0].telegram_message_id == 55
    assert stats[0].total_reactions == 2
    assert stats[0].interested_reactions == 1
    assert stats[0].not_interested_reactions == 1
    assert stats[0].interested_ratio == 0.5


async def test_user_category_preferences_roundtrip(db_session) -> None:
    user = await UserRepository(db_session).get_or_create(telegram_id=600, username="owner")
    repo = UserCategoryPreferenceRepository(db_session)

    await repo.set_enabled(user.id, "AI tools", True)
    await repo.set_enabled(user.id, "Business", False)

    enabled = await repo.enabled_categories(user.id)
    preferences = await repo.all_preferences(user.id)

    assert enabled == ["AI tools"]
    assert len(preferences) == 2
    assert {pref.category: pref.is_enabled for pref in preferences} == {
        "AI tools": True,
        "Business": False,
    }
