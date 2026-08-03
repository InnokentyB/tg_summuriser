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


def test_find_duplicate_matches_rewritten_news_with_shared_facts() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "OpenAI представила GPT-5 mini для агентских сценариев и автоматизации "
        "рабочих процессов. Подробнее: https://t.me/ai_news/42",
    )
    existing = [
        make_post(
            1,
            "Компания OpenAI выпустила модель GPT-5 mini для AI-агентов и "
            "автоматизации workflows.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_uses_ai_summary_when_original_text_is_rewritten() -> None:
    dedup = Deduplicator()
    current = make_post(2, "Короткий пост с другим текстом")
    current.summary = "OpenAI выпустила GPT-5 mini для AI-агентов и автоматизации."
    existing = [make_post(1, "Совсем другая формулировка новости")]
    existing[0].summary = "OpenAI представила GPT-5 mini для агентских сценариев и автоматизации."

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id == 1


def test_find_duplicate_ignores_different_posts() -> None:
    dedup = Deduplicator()
    current = make_post(2, "A deep dive into business moats")
    existing = [make_post(1, "Prompt engineering techniques for tool use")]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None


def test_find_duplicate_keeps_related_but_different_news_separate() -> None:
    dedup = Deduplicator()
    current = make_post(
        2,
        "OpenAI купила стартап для генерации видео и планирует интегрировать команду.",
    )
    existing = [
        make_post(
            1,
            "OpenAI выпустила GPT-5 mini для AI-агентов и автоматизации workflows.",
        )
    ]

    duplicate_id = dedup.find_duplicate(current, existing)

    assert duplicate_id is None
