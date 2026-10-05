import httpx
from openai import RateLimitError

from tg_summariser.services.quota_alert import QuotaAlertGuard, is_insufficient_quota_error


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


def quota_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(429, request=request)
    return RateLimitError(
        message="You have no credits remaining.",
        response=response,
        body={"error": {"code": "credit_balance_exhausted"}},
    )


def test_detects_credit_balance_exhaustion() -> None:
    assert is_insufficient_quota_error(quota_error()) is True
    assert is_insufficient_quota_error(RuntimeError("temporary timeout")) is False


async def test_quota_alert_is_sent_once_until_recovery() -> None:
    bot = FakeBot()
    guard = QuotaAlertGuard()

    assert await guard.notify_once(bot, 42) is True
    assert await guard.notify_once(bot, 42) is False
    guard.mark_recovered()
    assert await guard.notify_once(bot, 42) is True

    assert len(bot.messages) == 2
    assert "Закончились кредиты" in bot.messages[0][1]
