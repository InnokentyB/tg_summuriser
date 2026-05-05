from tg_summariser.models import Post, PostStatus
from tg_summariser.services.scoring import RelevanceScorer


def make_post(**kwargs) -> Post:
    defaults = {
        "channel_id": 1,
        "telegram_message_id": 10,
        "raw_text": "AI agents are getting better",
        "normalized_text": "AI agents are getting better",
        "relevance_score": 0.5,
        "importance_score": 0.6,
        "category": "AI & Agents",
        "explanation": "Base explanation.",
    }
    defaults.update(kwargs)
    return Post(**defaults)


def test_score_promotes_affinity_matches() -> None:
    scorer = RelevanceScorer()
    post = make_post()

    score, status, explanation = scorer.score(
        post,
        category_affinity={"AI & Agents": 2.0},
        channel_affinity={1: 1.0},
    )

    assert score > 0.5
    assert status == PostStatus.processed
    assert "Категория уже часто нравится пользователю." in explanation
    assert "Источник уже показывал интересные посты." in explanation


def test_score_hides_duplicates() -> None:
    scorer = RelevanceScorer()
    post = make_post(duplicate_of_post_id=99)

    score, status, explanation = scorer.score(post, {}, {})

    assert score < 0.5
    assert status == PostStatus.hidden
    assert "Пост скрыт как почти дубликат." in explanation


def test_score_hides_low_relevance_post() -> None:
    scorer = RelevanceScorer()
    post = make_post(relevance_score=0.2)

    score, status, explanation = scorer.score(post, {}, {})

    assert score == 0.2
    assert status == PostStatus.hidden
    assert "Слабая релевантность по текущим сигналам." in explanation

