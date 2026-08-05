from tg_summariser.config import settings
from tg_summariser.models import Post, PostStatus
from tg_summariser.services.prefilter import LocalPrefilter


def make_post(text: str, channel_id: int = 1) -> Post:
    return Post(
        id=1,
        channel_id=channel_id,
        telegram_message_id=1,
        raw_text=text,
        normalized_text=text,
    )


def test_prefilter_hides_promo_without_topic_signal(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_prefilter_enabled", True)
    monkeypatch.setattr(settings, "ai_prefilter_strict", False)
    prefilter = LocalPrefilter()

    decision = prefilter.decide(
        make_post("Подписывайтесь на вебинар, скидка 50 процентов только сегодня"),
        channel_affinity={},
    )

    assert decision.should_call_ai is False
    assert decision.forced_status == PostStatus.hidden
    assert decision.ai_result is not None
    assert decision.ai_result.category == "Filtered"


def test_prefilter_allows_topic_signal_even_with_promo_marker(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_prefilter_enabled", True)
    monkeypatch.setattr(settings, "ai_prefilter_strict", False)
    prefilter = LocalPrefilter()

    decision = prefilter.decide(
        make_post("OpenAI выпустила важное обновление для AI agents, подробности в вебинаре"),
        channel_affinity={},
    )

    assert decision.should_call_ai is True


def test_strict_prefilter_allows_trusted_channel_without_keywords(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_prefilter_enabled", True)
    monkeypatch.setattr(settings, "ai_prefilter_strict", True)
    prefilter = LocalPrefilter()

    decision = prefilter.decide(
        make_post("Длинный пост без явных ключевых слов, но из полезного источника", channel_id=42),
        channel_affinity={42: 1.0},
    )

    assert decision.should_call_ai is True
