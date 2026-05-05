from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from aiogram import Bot

from tg_summariser.bot.keyboards import feedback_keyboard
from tg_summariser.models import Post
from tg_summariser.services.repositories import DigestRepository, PostRepository


class DigestService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_digest(self, session, user_id: int, telegram_id: int) -> int:
        post_repo = PostRepository(session)
        digest_repo = DigestRepository(session)
        posts = await post_repo.top_candidates(limit=5)
        if not posts:
            await self.bot.send_message(telegram_id, "Новых релевантных постов для дайджеста пока нет.")
            return 0

        digest = await digest_repo.create_digest(user_id=user_id, scheduled_for=datetime.utcnow())
        grouped: dict[str, list[Post]] = defaultdict(list)
        for post in posts:
            grouped[post.category or "Без категории"].append(post)

        rank = 1
        for category, category_posts in grouped.items():
            await self.bot.send_message(telegram_id, f"<b>{category}</b>")
            for post in category_posts:
                post.was_sent = True
                await digest_repo.add_item(digest.id, post.id, rank)
                await self.bot.send_message(
                    telegram_id,
                    self._render_post(post),
                    reply_markup=feedback_keyboard(post.id),
                    disable_web_page_preview=True,
                )
                rank += 1
        return len(posts)

    def _render_post(self, post: Post) -> str:
        link = post.original_link or "Ссылка недоступна"
        return (
            f"• <b>{post.summary or 'Без саммари'}</b>\n"
            f"Почему важно: {post.why_important or 'Без пояснения'}\n"
            f"Важность: {post.importance_score:.2f} | Источник: {post.channel.title}\n"
            f"Решение: {post.explanation or 'Без пояснения'}\n"
            f"Ссылка: {link}"
        )
