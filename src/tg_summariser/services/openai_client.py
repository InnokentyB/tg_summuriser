from openai import AsyncOpenAI

from tg_summariser.config import settings


def build_openai_client() -> AsyncOpenAI | None:
    """Build one consistently configured OpenAI-compatible async client."""
    if not settings.openai_api_key:
        return None

    kwargs: dict[str, str] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url.rstrip("/")
    return AsyncOpenAI(**kwargs)
