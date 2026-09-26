from __future__ import annotations

import json
import logging
from html import escape

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.bot.keyboards import feedback_keyboard
from tg_summariser.config import settings
from tg_summariser.models import Post, PostStatus
from tg_summariser.schemas import ProductMatch

logger = logging.getLogger(__name__)


def serialize_product_matches(matches: list[ProductMatch]) -> str | None:
    """Serialize a list of ProductMatch items to a JSON string or None."""
    if not matches:
        return None
    return json.dumps(
        [
            {
                "product": match.product,
                "score": match.score,
                "why_useful": match.why_useful,
                "suggested_use": match.suggested_use,
            }
            for match in matches
        ],
        ensure_ascii=False,
    )


def deserialize_product_matches(value: str | None) -> list[ProductMatch]:
    """Deserialize a JSON string into a list of ProductMatch items."""
    if not value:
        return []
    try:
        items = json.loads(value)
        return [
            ProductMatch(
                product=str(item["product"]),
                score=float(item["score"]),
                why_useful=str(item.get("why_useful", "")),
                suggested_use=str(item.get("suggested_use", "")),
            )
            for item in items
            if isinstance(item, dict)
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("Failed deserializing product matches JSON: %s", exc)
        return []


class ProductRadarService:
    """Delivers product radar match alerts to Telegram users."""

    def __init__(self, bot: Bot) -> None:
        """Initialize product radar with Telegram Bot instance."""
        self.bot = bot

    async def send_review(self, session: AsyncSession, telegram_id: int) -> int:
        """Find matching product review posts and send alert cards to the user."""
        if not settings.product_radar_enabled:
            return 0
        result = await session.execute(
            select(Post)
            .options(selectinload(Post.channel))
            .where(
                Post.status == PostStatus.processed,
                Post.is_promotional.is_(False),
                Post.product_review_sent.is_(False),
                Post.product_matches_json.is_not(None),
                Post.original_link.is_not(None),
            )
            .order_by(Post.relevance_score.desc(), Post.importance_score.desc())
        )
        selected = []
        for post in result.scalars():
            matches = [
                match
                for match in deserialize_product_matches(post.product_matches_json)
                if match.score >= settings.product_radar_min_score
            ]
            if matches:
                selected.append((post, matches))

        if not selected:
            return 0
        await self.bot.send_message(
            telegram_id,
            "<b>Продуктовый радар</b>\nМатериалы, которые могут пригодиться вашим продуктам:",
        )
        for post, matches in selected:
            await self.bot.send_message(
                telegram_id,
                self._render(post, matches),
                reply_markup=feedback_keyboard(post),
                disable_web_page_preview=True,
            )
            post.product_review_sent = True
            await session.commit()
        return len(selected)

    @staticmethod
    def _render(post: Post, matches: list[ProductMatch]) -> str:
        lines = []
        for match in sorted(matches, key=lambda item: item.score, reverse=True):
            lines.append(f"<b>{escape(match.product)}</b> · {match.score:.2f}")
            lines.append(f"Почему полезно: {escape(match.why_useful or 'Без пояснения')}")
            lines.append(f"Как применить: {escape(match.suggested_use or 'Требует разбора')}")
        summary = escape(post.summary or post.raw_text[:240])
        source = escape(post.channel.title)
        link = escape(post.original_link or "Ссылка недоступна", quote=True)
        match_text = "\n".join(lines)
        return (
            f"{match_text}\n\n"
            f"<b>{summary}</b>\n"
            f"Источник: {source}\n"
            f"Ссылка: {link}"
        )
