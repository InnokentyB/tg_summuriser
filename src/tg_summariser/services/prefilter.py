from __future__ import annotations

import re
from dataclasses import dataclass

from tg_summariser.config import settings
from tg_summariser.models import Post, PostStatus
from tg_summariser.schemas import ProcessedPost

_URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


@dataclass(slots=True)
class PrefilterDecision:
    should_call_ai: bool
    ai_result: ProcessedPost | None = None
    forced_status: PostStatus | None = None
    explanation: str = ""


class LocalPrefilter:
    def __init__(self) -> None:
        self.positive_keywords = self._parse_keywords(settings.ai_prefilter_positive_keywords)
        self.negative_keywords = self._parse_keywords(settings.ai_prefilter_negative_keywords)

    def decide(
        self,
        post: Post,
        *,
        channel_affinity: dict[int, float],
    ) -> PrefilterDecision:
        if not settings.ai_prefilter_enabled:
            return PrefilterDecision(should_call_ai=True)

        text = " ".join(post.raw_text.split())
        lowered = text.casefold()
        word_count = len(_WORD_RE.findall(lowered))
        positive_matches = self._matches(lowered, self.positive_keywords)
        negative_matches = self._matches(lowered, self.negative_keywords)
        trusted_channel = channel_affinity.get(post.channel_id, 0) > 0

        if self._is_link_only(lowered, word_count):
            return self._hidden(text, "Локальный prefilter: пост похож на короткий link-only без контекста.")

        if negative_matches and not positive_matches and not trusted_channel:
            return self._hidden(
                text,
                "Локальный prefilter: рекламные/промо-маркеры без тематических сигналов.",
            )

        if settings.ai_prefilter_strict and not positive_matches and not trusted_channel:
            return self._hidden(
                text,
                "Локальный prefilter: в строгом режиме нет совпадений с приоритетными темами.",
            )

        return PrefilterDecision(should_call_ai=True)

    def _hidden(self, text: str, explanation: str) -> PrefilterDecision:
        return PrefilterDecision(
            should_call_ai=False,
            ai_result=ProcessedPost(
                language="ru" if self._has_cyrillic(text) else "en",
                summary=text[:180] + ("..." if len(text) > 180 else ""),
                why_important="Пост не прошёл локальный prefilter перед AI-обработкой.",
                category="Filtered",
                importance_score=0.1,
                relevance_score=0.1,
                explanation=explanation,
            ),
            forced_status=PostStatus.hidden,
            explanation=explanation,
        )

    @staticmethod
    def _parse_keywords(value: str) -> list[str]:
        return [item.strip().casefold() for item in value.split(",") if item.strip()]

    @staticmethod
    def _matches(text: str, keywords: list[str]) -> list[str]:
        return [keyword for keyword in keywords if keyword in text]

    @staticmethod
    def _is_link_only(text: str, word_count: int) -> bool:
        return bool(_URL_RE.search(text)) and word_count <= 8

    @staticmethod
    def _has_cyrillic(text: str) -> bool:
        return any(ch in text.casefold() for ch in "абвгдежзийклмнопрстуфхцчшщыэюя")
