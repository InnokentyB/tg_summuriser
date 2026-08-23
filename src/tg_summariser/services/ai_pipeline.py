from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, RateLimitError

from tg_summariser.config import settings
from tg_summariser.schemas import ProcessedPost


class AIPipeline:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        self.api_disabled_reason: str | None = None

    async def process_post(self, text: str) -> ProcessedPost:
        return (await self.process_posts([(0, text)]))[0]

    async def process_posts(self, posts: list[tuple[int, str]]) -> dict[int, ProcessedPost]:
        results: dict[int, ProcessedPost] = {}
        api_posts: list[tuple[int, str]] = []
        clean_posts = {post_id: " ".join(text.split()) for post_id, text in posts}

        for post_id, clean_text in clean_posts.items():
            if (
                not self.client
                or self.api_disabled_reason
                or len(clean_text) < settings.ai_min_text_length
            ):
                results[post_id] = self._fallback(clean_text)
            else:
                api_posts.append((post_id, self._trim_for_api(clean_text)))

        for start in range(0, len(api_posts), max(settings.ai_batch_size, 1)):
            batch = api_posts[start : start + max(settings.ai_batch_size, 1)]
            results.update(await self._process_api_batch(batch, clean_posts))
        return results

    async def _process_api_batch(
        self,
        posts: list[tuple[int, str]],
        clean_posts: dict[int, str],
    ) -> dict[int, ProcessedPost]:
        prompt = self.build_prompt(posts)

        try:
            response = await self.client.responses.create(
                model=settings.openai_model,
                input=prompt,
            )
        except RateLimitError as exc:
            if self._is_insufficient_quota(exc):
                self.api_disabled_reason = "insufficient_quota"
                return {post_id: self._fallback(clean_posts[post_id]) for post_id, _ in posts}
            raise
        return self.parse_results(self._extract_text(response), posts, clean_posts)

    def build_prompt(self, posts: list[tuple[int, str]]) -> str:
        return (
            "You process Telegram channel posts for a personal digest.\n"
            "Return strict JSON object with a results array. Return exactly one result per input "
            "post and preserve its integer id. Each result must contain: id, language, summary, "
            "why_important, category, importance_score, relevance_score, explanation, "
            "is_promotional.\n"
            "Use Russian for fields summary, why_important, explanation.\n"
            "Keep summary and why_important concise.\n"
            "Set scores from 0 to 1.\n"
            "Set is_promotional=true for ads, sponsorships, sales pitches, event/course promotion, "
            "affiliate content, or calls to buy/subscribe; otherwise false.\n"
            f"Posts:\n{json.dumps([{'id': post_id, 'text': text} for post_id, text in posts], ensure_ascii=False)}"
        )

    def parse_results(
        self,
        content: str,
        posts: list[tuple[int, str]],
        clean_posts: dict[int, str] | None = None,
        fallback_missing: bool = True,
    ) -> dict[int, ProcessedPost]:
        clean_posts = clean_posts or {post_id: " ".join(text.split()) for post_id, text in posts}
        expected_ids = {post_id for post_id, _ in posts}
        parsed_results: dict[int, ProcessedPost] = {}
        try:
            parsed = json.loads(content)
            items = parsed.get("results", [])
            if not isinstance(items, list):
                raise TypeError("results must be a list")
            for item in items:
                try:
                    post_id = int(item["id"])
                    if post_id not in expected_ids or post_id in parsed_results:
                        continue
                    clean_text = clean_posts[post_id]
                    parsed_results[post_id] = self._processed_post(item, clean_text)
                except (KeyError, ValueError, TypeError):
                    continue
        except (AttributeError, ValueError, TypeError, json.JSONDecodeError):
            parsed_results = {}

        if fallback_missing:
            for post_id in expected_ids - parsed_results.keys():
                parsed_results[post_id] = self._fallback(clean_posts[post_id])
        return parsed_results

    def _processed_post(self, parsed: dict[str, Any], clean_text: str) -> ProcessedPost:
        return ProcessedPost(
            language=parsed.get("language", "unknown"),
            summary=parsed.get("summary", clean_text[:180]),
            why_important=parsed.get("why_important", "Может быть полезно для общего контекста."),
            category=parsed.get("category", "General"),
            importance_score=float(parsed.get("importance_score", 0.5)),
            relevance_score=float(parsed.get("relevance_score", 0.5)),
            explanation=parsed.get("explanation", "Добавлен по базовой AI-оценке."),
            is_promotional=self._as_bool(parsed.get("is_promotional", False)),
        )

    def _trim_for_api(self, text: str) -> str:
        if len(text) <= settings.ai_max_input_chars:
            return text
        return text[: settings.ai_max_input_chars].rstrip()

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
            is_promotional=False,
        )

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"true", "1", "yes"}

    @staticmethod
    def _extract_text(response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text
        for item in getattr(response, "output", []):
            for content in getattr(item, "content", []):
                if getattr(content, "type", "") == "output_text":
                    return content.text
        return "{}"

    @staticmethod
    def _is_insufficient_quota(exc: RateLimitError) -> bool:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                return error.get("code") in {"insufficient_quota", "credit_balance_exhausted"}
        return "insufficient_quota" in str(exc) or "credit_balance_exhausted" in str(exc)
