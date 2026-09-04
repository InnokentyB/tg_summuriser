from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, RateLimitError

from tg_summariser.config import settings
from tg_summariser.schemas import ProcessedPost, ProductMatch

_PRODUCT_PROFILES = (
    "Product fit profiles:\n"
    "- Контент-завод: AI-assisted content production, editorial quality gates, content reuse, "
    "multi-channel adaptation, distribution automation, and content analytics.\n"
    "- Seturon: decision-first adaptive learning, L&D and enablement, onboarding, role-aware "
    "learning paths, course authoring, and reducing time-to-competency.\n"
    "- Подмастерье аналитика: source-grounded business/system analysis, requirements, "
    "traceability, contradiction and risk detection, deterministic evals, and reliable agents.\n"
)


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
        results = self.parse_results(
            self._extract_text(response),
            posts,
            clean_posts,
            fallback_missing=False,
        )
        invalid_ids = [
            post_id for post_id, result in results.items() if self._has_ukrainian_output(result)
        ]
        for post_id in invalid_ids:
            del results[post_id]

        if invalid_ids:
            retry_posts = [(post_id, dict(posts)[post_id]) for post_id in invalid_ids]
            retry_prompt = (
                self.build_prompt(retry_posts)
                + "\nCRITICAL CORRECTION: The previous answer used Ukrainian. Generate all user-facing "
                "fields in Russian only. Translate the source; do not copy its language."
            )
            retry_response = await self.client.responses.create(
                model=settings.openai_model,
                input=retry_prompt,
            )
            retry_results = self.parse_results(
                self._extract_text(retry_response),
                retry_posts,
                clean_posts,
                fallback_missing=False,
            )
            results.update(
                {
                    post_id: result
                    for post_id, result in retry_results.items()
                    if not self._has_ukrainian_output(result)
                }
            )

        for post_id, _ in posts:
            if post_id not in results:
                results[post_id] = (
                    self._language_failure_fallback()
                    if post_id in invalid_ids
                    else self._fallback(clean_posts[post_id])
                )
        return results

    def build_prompt(self, posts: list[tuple[int, str]]) -> str:
        return (
            "You process Telegram channel posts for a personal digest.\n"
            "Return strict JSON object with a results array. Return exactly one result per input "
            "post and preserve its integer id. Each result must contain: id, language, summary, "
            "why_important, category, importance_score, relevance_score, explanation, "
            "is_promotional, product_matches.\n"
            "product_matches must be an array containing only genuinely useful matches. Each "
            "match must contain product, score (0 to 1), why_useful, suggested_use. Use the exact "
            "product names from the profiles. Return [] when there is no concrete product use.\n"
            "For newsletter roundups, return a product match only when the relevant item has a "
            "direct actionable source link; a newsletter, tracking, or social-profile link is not enough.\n"
            f"{_PRODUCT_PROFILES}"
            "Regardless of the source language, write summary, why_important, explanation, "
            "and all product_matches text in Russian only. Never answer in Ukrainian. Translate "
            "Ukrainian and English source material into Russian. The language field must describe "
            "the original source language.\n"
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
            product_matches=self._product_matches(parsed.get("product_matches", [])),
        )

    @staticmethod
    def _product_matches(value: Any) -> list[ProductMatch]:
        if not isinstance(value, list):
            return []
        allowed_products = {"Контент-завод", "Seturon", "Подмастерье аналитика"}
        matches = []
        for item in value:
            if not isinstance(item, dict) or item.get("product") not in allowed_products:
                continue
            try:
                score = max(0.0, min(float(item.get("score", 0)), 1.0))
            except (TypeError, ValueError):
                continue
            matches.append(
                ProductMatch(
                    product=item["product"],
                    score=score,
                    why_useful=str(item.get("why_useful", "")).strip(),
                    suggested_use=str(item.get("suggested_use", "")).strip(),
                )
            )
        return matches

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
    def _language_failure_fallback() -> ProcessedPost:
        return ProcessedPost(
            language="uk",
            summary="Материал требует повторной обработки для перевода на русский язык.",
            why_important="Некорректный языковой результат не включён в дайджест.",
            category="General",
            importance_score=0.0,
            relevance_score=0.0,
            explanation="AI дважды вернул текст не на русском языке.",
            is_promotional=False,
        )

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"true", "1", "yes"}

    @staticmethod
    def _has_ukrainian_output(result: ProcessedPost) -> bool:
        user_facing_text = " ".join(
            [
                result.summary,
                result.why_important,
                result.explanation,
                *(match.why_useful for match in result.product_matches),
                *(match.suggested_use for match in result.product_matches),
            ]
        ).casefold()
        return any(character in user_facing_text for character in "іїєґ")

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
