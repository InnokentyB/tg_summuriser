from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram import Bot

from tg_summariser.bot.keyboards import feedback_keyboard
from tg_summariser.models import Post
from tg_summariser.services.repositories import (
    DigestRepository,
    PostRepository,
    UserCategoryPreferenceRepository,
)


class DigestService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_digest(self, session, user_id: int, telegram_id: int) -> int:
        post_repo = PostRepository(session)
        enabled_categories = await UserCategoryPreferenceRepository(session).enabled_categories(user_id)
        posts = await post_repo.top_candidates(limit=5, categories=enabled_categories or None)
        if not posts:
            if enabled_categories:
                category_list = ", ".join(enabled_categories)
                await self.bot.send_message(
                    telegram_id,
                    f"Новых релевантных постов для выбранных категорий пока нет: {category_list}",
                )
            else:
                await self.bot.send_message(telegram_id, "Новых релевантных постов для дайджеста пока нет.")
            return 0

        return await self.send_posts(
            session=session,
            user_id=user_id,
            telegram_id=telegram_id,
            posts=posts,
        )

    async def send_channel_welcome_digest(
        self,
        session,
        user_id: int,
        telegram_id: int,
        channel_id: int,
        channel_title: str,
        notify_empty: bool = True,
    ) -> int:
        posts = await PostRepository(session).top_candidates_for_channel(channel_id=channel_id, limit=5)
        if not posts:
            if notify_empty:
                await self.bot.send_message(
                    telegram_id,
                    f"Канал '{channel_title}' добавлен. Подходящих постов для стартового саммари пока не нашлось.",
                )
            return 0

        await self.bot.send_message(
            telegram_id,
            f"Канал '{channel_title}' добавлен. Вот стартовое саммари по последним постам.",
        )
        return await self.send_posts(
            session=session,
            user_id=user_id,
            telegram_id=telegram_id,
            posts=posts,
        )

    async def send_posts(self, session, user_id: int, telegram_id: int, posts: list[Post]) -> int:
        digest_repo = DigestRepository(session)

        digest = await digest_repo.create_digest(user_id=user_id, scheduled_for=datetime.utcnow())
        rank = 1
        for post in posts:
            post.was_sent = True
            await digest_repo.add_item(digest.id, post.id, rank)
            await self.bot.send_message(
                telegram_id,
                self._render_post(post),
                reply_markup=feedback_keyboard(post),
                disable_web_page_preview=True,
            )
            rank += 1
        return len(posts)

    def _render_post(self, post: Post) -> str:
        link = escape(post.original_link or "Ссылка недоступна", quote=True)
        category_tag = self._category_tag(post.category)
        summary = escape(post.summary or "Без саммари")
        why_important = escape(self._shorten(post.why_important or "Без пояснения", limit=160))
        source_title = escape(post.channel.title)
        return (
            f"{category_tag} <b>{summary}</b>\n"
            f"Почему важно: {why_important}\n"
            f"Важность: {post.importance_score:.2f} | Источник: {source_title}\n"
            f"Ссылка: {link}"
        )

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "..."

    @staticmethod
    def _category_tag(category: str | None) -> str:
        label = (category or "Без категории").strip()
        safe = "".join(ch if ch.isalnum() else "_" for ch in label).strip("_")
        safe = safe[:32] or "without_category"
        return f"<code>#{safe}</code>"
