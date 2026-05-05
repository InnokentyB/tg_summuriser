from tg_summariser.models import Post
from tg_summariser.services.dedup import Deduplicator


def make_post(post_id: int, text: str) -> Post:
    return Post(
        id=post_id,
        channel_id=1,
        telegram_message_id=post_id,
        raw_text=text,
        normalized_text=text,
    )


def test_find_duplicate_returns_matching_post_id() -> None:
    dedup = Deduplicator()
    current = make_post(2, "OpenAI released a new model for agent workflows today")
    existing = [make_post(1, "OpenAI released a new model for agent workflows today")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_ignores_different_posts() -> None:
    dedup = Deduplicator()
    current = make_post(2, "A deep dive into business moats")
    existing = [make_post(1, "Prompt engineering techniques for tool use")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None

