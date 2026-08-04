import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery

from tg_summariser.bot.handlers import parse_search_args, safe_callback_answer


class CallbackStub:
    def __init__(self, error: TelegramBadRequest | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, bool]] = []

    async def answer(self, text: str, *, show_alert: bool = False) -> None:
        self.calls.append((text, show_alert))
        if self.error:
            raise self.error


def test_parse_search_args_with_filters() -> None:
    query, category, channel = parse_search_args(
        "ai agents; category=AI & Agents; channel=Latent Space"
    )
    assert query == "ai agents"
    assert category == "AI & Agents"
    assert channel == "Latent Space"


def test_parse_search_args_without_filters() -> None:
    query, category, channel = parse_search_args("business models")
    assert query == "business models"
    assert category is None
    assert channel is None


async def test_safe_callback_answer_ignores_stale_query() -> None:
    callback = CallbackStub(
        TelegramBadRequest(
            method=AnswerCallbackQuery(callback_query_id="old"),
            message="Bad Request: query is too old and response timeout expired or query ID is invalid",
        )
    )

    await safe_callback_answer(callback, "Оценка сохранена.")

    assert callback.calls == [("Оценка сохранена.", False)]


async def test_safe_callback_answer_reraises_other_bad_requests() -> None:
    callback = CallbackStub(
        TelegramBadRequest(
            method=AnswerCallbackQuery(callback_query_id="broken"),
            message="Bad Request: unsupported callback response",
        )
    )

    with pytest.raises(TelegramBadRequest):
        await safe_callback_answer(callback, "Оценка сохранена.")
