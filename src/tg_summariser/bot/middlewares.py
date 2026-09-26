from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    """Restricts access to the bot to the configured OWNER_TELEGRAM_ID."""

    def __init__(self, owner_telegram_id: int | None) -> None:
        """Initialize middleware with owner telegram id."""
        self.owner_telegram_id = owner_telegram_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, object]], Awaitable[object]],
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        """Inspect event sender and enforce owner whitelist if configured."""
        if not self.owner_telegram_id:
            return await handler(event, data)

        from_user = getattr(event, "from_user", None)
        user_id = getattr(from_user, "id", None) if from_user else None

        if user_id != self.owner_telegram_id:
            logger.warning(
                "Blocked unauthorized access attempt: user_id=%s, username=%s, event_type=%s",
                user_id,
                getattr(from_user, "username", None) if from_user else None,
                type(event).__name__,
            )
            if isinstance(event, Message):
                await event.answer("Этот MVP пока доступен только владельцу.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ только для владельца.", show_alert=True)
            return None

        return await handler(event, data)
