from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from tg_summariser.config import settings
from tg_summariser.schemas import ProcessedPost


class AIPipeline:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def process_post(self, text: str) -> ProcessedPost:
        clean_text = " ".join(text.split())
        if not self.client:
            return self._fallback(clean_text)

        prompt = (
            "You process Telegram channel posts for a personal digest.\n"
            "Return strict JSON with keys: language, summary, why_important, category, "
            "importance_score, relevance_score, explanation.\n"
            "Use Russian for fields summary, why_important, explanation.\n"
            "Keep summary and why_important concise.\n"
            "Set scores from 0 to 1.\n"
            f"Post:\n{clean_text}"
        )

        response = await self.client.responses.create(
            model=settings.openai_model,
            input=prompt,
        )
        content = self._extract_text(response)
        try:
            parsed = json.loads(content)
            return ProcessedPost(
                language=parsed.get("language", "unknown"),
                summary=parsed.get("summary", clean_text[:180]),
                why_important=parsed.get("why_important", "Может быть полезно для общего контекста."),
                category=parsed.get("category", "General"),
                importance_score=float(parsed.get("importance_score", 0.5)),
                relevance_score=float(parsed.get("relevance_score", 0.5)),
                explanation=parsed.get("explanation", "Добавлен по базовой AI-оценке."),
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._fallback(clean_text)

    def _fallback(self, text: str) -> ProcessedPost:
        lowered = text.lower()
        category = "Business"
        if any(token in lowered for token in ["ai", "llm", "agent", "агент", "ии", "gpt"]):
            category = "AI & Agents"
        summary = text[:180] + ("..." if len(text) > 180 else "")
        why = "Пост может быть релевантен вашим основным темам или источникам."
        explanation = "Добавлен по fallback-логике: текст совпадает с приоритетными темами."
        return ProcessedPost(
            language="ru" if any(ch in lowered for ch in "абвгдежзийклмнопрстуфхцчшщыэюя") else "en",
            summary=summary,
            why_important=why,
            category=category,
            importance_score=0.55,
            relevance_score=0.6 if category == "AI & Agents" else 0.45,
            explanation=explanation,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        for item in getattr(response, "output", []):
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    return content.text
        return "{}"

