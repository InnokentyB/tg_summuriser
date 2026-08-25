from tg_summariser.config import settings
from tg_summariser.models import PostStatus
from tg_summariser.schemas import ProductMatch
from tg_summariser.services.product_radar import (
    ProductRadarService,
    serialize_product_matches,
)
from tg_summariser.services.repositories import ChannelRepository, PostRepository


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


async def test_product_radar_sends_matching_post_once(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "product_radar_enabled", True)
    monkeypatch.setattr(settings, "product_radar_min_score", 0.65)
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9100,
        title="Product Research",
        telegram_username="product_research",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=1,
        raw_text="Research about adaptive onboarding and source-grounded agents",
        normalized_text="adaptive onboarding source grounded agents",
        original_link="https://t.me/product_research/1",
    )
    post.status = PostStatus.processed
    post.summary = "Полезное исследование"
    post.product_matches_json = serialize_product_matches(
        [
            ProductMatch("Seturon", 0.9, "Про адаптивный онбординг", "Проверить ACDF-сценарий"),
            ProductMatch(
                "Подмастерье аналитика",
                0.8,
                "Про проверяемые источники",
                "Добавить eval-сценарий",
            ),
        ]
    )
    bot = FakeBot()
    service = ProductRadarService(bot)

    first = await service.send_review(db_session, telegram_id=123)
    second = await service.send_review(db_session, telegram_id=123)

    assert first == 1
    assert second == 0
    assert len(bot.messages) == 2
    assert "Продуктовый радар" in bot.messages[0][1]
    assert "Seturon" in bot.messages[1][1]
    assert "Подмастерье аналитика" in bot.messages[1][1]
    assert post.product_review_sent is True


async def test_product_radar_ignores_matches_below_threshold(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "product_radar_enabled", True)
    monkeypatch.setattr(settings, "product_radar_min_score", 0.65)
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=9101,
        title="Weak Product Research",
        telegram_username="weak_product_research",
        is_private=False,
    )
    post, _ = await PostRepository(db_session).create_post(
        channel_id=channel.id,
        telegram_message_id=1,
        raw_text="A marginally related post",
        normalized_text="marginal relation",
        original_link="https://t.me/weak_product_research/1",
    )
    post.status = PostStatus.processed
    post.product_matches_json = serialize_product_matches(
        [ProductMatch("Контент-завод", 0.4, "Слабая связь", "Не использовать")]
    )
    bot = FakeBot()

    sent = await ProductRadarService(bot).send_review(db_session, telegram_id=123)

    assert sent == 0
    assert bot.messages == []
    assert post.product_review_sent is False
