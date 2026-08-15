from tg_summariser.models import Channel, Post
from tg_summariser.services.digest_service import DigestService


class DummyBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))
        return None


def test_render_post_uses_inline_category_tag_and_short_why() -> None:
    service = DigestService(DummyBot())
    channel = Channel(telegram_chat_id=1, title="AI Product", telegram_username="ai_product")
    post = Post(
        channel_id=1,
        telegram_message_id=10,
        raw_text="raw",
        normalized_text="raw",
        summary="Короткое саммари",
        why_important="Это очень длинное пояснение " * 20,
        category="AI tools",
        importance_score=0.7,
        original_link="https://t.me/ai_product/10",
    )
    post.channel = channel

    rendered = service._render_post(post)

    assert "<code>#AI_tools</code>" in rendered
    assert "Решение:" not in rendered
    assert "Почему важно:" in rendered
    assert "Ссылка: https://t.me/ai_product/10" in rendered


def test_render_post_escapes_html_from_article_content() -> None:
    service = DigestService(DummyBot())
    channel = Channel(telegram_chat_id=1, title="AI <News>", telegram_username="ai_news")
    post = Post(
        channel_id=1,
        telegram_message_id=11,
        raw_text="raw",
        normalized_text="raw",
        summary="Use <key> & value",
        why_important="Compare <old> with <new>",
        category="AI tools",
        importance_score=0.8,
        original_link="https://example.com/?a=1&b=2",
    )
    post.channel = channel

    rendered = service._render_post(post)

    assert "<key>" not in rendered
    assert "Use &lt;key&gt; &amp; value" in rendered
    assert "AI &lt;News&gt;" in rendered
    assert "https://example.com/?a=1&amp;b=2" in rendered


async def test_channel_welcome_digest_can_skip_empty_notification(db_session) -> None:
    bot = DummyBot()
    service = DigestService(bot)

    sent = await service.send_channel_welcome_digest(
        session=db_session,
        user_id=1,
        telegram_id=100,
        channel_id=1,
        channel_title="Empty Channel",
        notify_empty=False,
    )

    assert sent == 0
    assert bot.messages == []
