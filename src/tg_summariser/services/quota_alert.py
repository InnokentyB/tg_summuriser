from __future__ import annotations

from aiogram import Bot


def is_insufficient_quota_error(exc: BaseException) -> bool:
    """Return whether an API exception means that paid credits are exhausted."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code") in {
            "insufficient_quota",
            "credit_balance_exhausted",
            "billing_hard_limit_reached",
        }:
            return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "insufficient_quota",
            "credit_balance_exhausted",
            "billing_hard_limit_reached",
            "no credits remaining",
        )
    )


class QuotaAlertGuard:
    """Send one owner alert per continuous OpenAI quota outage."""

    def __init__(self) -> None:
        self._alerted = False

    async def notify_once(self, bot: Bot, chat_id: int) -> bool:
        if self._alerted:
            return False
        await bot.send_message(
            chat_id,
            "Закончились кредиты OpenAI API. Обработка новых материалов приостановлена, "
            "а записи остаются в очереди. После пополнения баланса обработка продолжится "
            "автоматически.",
        )
        self._alerted = True
        return True

    def mark_recovered(self) -> None:
        self._alerted = False

    @property
    def alerted(self) -> bool:
        return self._alerted
