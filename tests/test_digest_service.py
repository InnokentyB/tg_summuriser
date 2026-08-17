import pytest
from sqlalchemy import select

from tg_summariser.models import Channel, DigestItem, Post, PostStatus
from tg_summariser.services.digest_service import DigestService
from tg_summariser.services.repositories import ChannelRepository, PostRepository, UserRepository


class DummyBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))
        return None


class FailingBot(DummyBot):
    async def send_message(self, *args, **kwargs):
        if self.messages:
            raise RuntimeError("Telegram unavailable")
        return await super().send_message(*args, **kwargs)


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


async def test_successful_posts_stay_sent_if_later_message_fails(db_session) -> None:
    user = await UserRepository(db_session).get_or_create(telegram_id=100)
    channel = await ChannelRepository(db_session).upsert_channel(
        telegram_chat_id=1,
        title="AI Product",
        telegram_username="ai_product",
        is_private=False,
    )
    repo = PostRepository(db_session)
    posts = []
    for message_id in (1, 2):
        post, _ = await repo.create_post(
            channel_id=channel.id,
            telegram_message_id=message_id,
            raw_text=f"Post {message_id}",
            normalized_text=f"Post {message_id}",
            original_link=f"https://t.me/ai_product/{message_id}",
        )
        post.status = PostStatus.processed
        post.summary = f"Summary {message_id}"
        post.channel = channel
        posts.append(post)

    with pytest.raises(RuntimeError, match="Telegram unavailable"):
        await DigestService(FailingBot()).send_posts(
            db_session,
            user.id,
            user.telegram_id,
            posts,
        )
    await db_session.rollback()

    stored_posts = list((await db_session.execute(select(Post).order_by(Post.id))).scalars())
    assert stored_posts[0].was_sent is True
    assert stored_posts[1].was_sent is False
    digest_items = list((await db_session.execute(select(DigestItem))).scalars())
    assert len(digest_items) == 1
