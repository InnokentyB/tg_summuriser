from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.config import settings
from tg_summariser.models import AIBatchJob, Post, PostStatus
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.prefilter import LocalPrefilter
from tg_summariser.services.product_radar import serialize_product_matches
from tg_summariser.services.repositories import FeedbackRepository, PostRepository
from tg_summariser.services.scoring import RelevanceScorer

_ACTIVE_STATUSES = {"validating", "in_progress", "finalizing"}
_FAILED_STATUSES = {"failed", "expired", "cancelled"}


class OpenAIBatchService:
    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self.client = client or (
            AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None
        )
        self.pipeline = AIPipeline()

    async def submit_pending(self, session: AsyncSession, user_id: int) -> int:
        if not settings.openai_batch_enabled or not self.client:
            return 0

        post_repo = PostRepository(session)
        await post_repo.hide_stale_pending(settings.digest_max_post_age_days)
        posts = await post_repo.pending_posts(limit=settings.openai_batch_post_limit)
        if not posts:
            return 0

        channel_affinity = await FeedbackRepository(session).channel_affinity(user_id)
        prefilter = LocalPrefilter()
        eligible: list[Post] = []
        for post in posts:
            decision = prefilter.decide(post, channel_affinity=channel_affinity)
            if decision.should_call_ai:
                eligible.append(post)
                continue
            if decision.ai_result:
                self._apply_ai_result(post, decision.ai_result)
            post.status = decision.forced_status or PostStatus.hidden
            post.explanation = decision.explanation or post.explanation

        if not eligible:
            return 0

        job = AIBatchJob(status="creating", post_count=len(eligible))
        session.add(job)
        await session.flush()
        for post in eligible:
            post.ai_batch_job_id = job.id

        content = self._build_jsonl(eligible)
        uploaded = await self.client.files.create(
            file=(f"tg-summariser-{job.id}.jsonl", content.encode(), "application/jsonl"),
            purpose="batch",
        )
        batch = await self.client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={"app": "tg-summariser", "job_id": str(job.id)},
        )
        job.input_file_id = uploaded.id
        job.openai_batch_id = batch.id
        job.status = batch.status
        job.updated_at = datetime.utcnow()
        return len(eligible)

    async def collect_completed(self, session: AsyncSession, user_id: int) -> int:
        if not settings.openai_batch_enabled or not self.client:
            return 0
        result = await session.execute(
            select(AIBatchJob).where(AIBatchJob.status.in_(_ACTIVE_STATUSES))
        )
        jobs = list(result.scalars())
        applied = 0
        for job in jobs:
            if not job.openai_batch_id:
                continue
            batch = await self.client.batches.retrieve(job.openai_batch_id)
            job.status = batch.status
            job.updated_at = datetime.utcnow()
            if batch.status == "completed" and batch.output_file_id:
                output = await self.client.files.content(batch.output_file_id)
                applied += await self._apply_output(session, job, output.text, user_id)
                job.completed_at = datetime.utcnow()
            elif batch.status in _FAILED_STATUSES:
                job.last_error = f"OpenAI batch ended with status {batch.status}"
                await self._release_posts(session, job.id)
        return applied

    def _build_jsonl(self, posts: list[Post]) -> str:
        lines = []
        size = max(settings.ai_batch_size, 1)
        for start in range(0, len(posts), size):
            group = posts[start : start + size]
            prompt_posts = [
                (post.id, self.pipeline._trim_for_api(" ".join(post.raw_text.split())))
                for post in group
            ]
            lines.append(
                json.dumps(
                    {
                        "custom_id": f"posts-{group[0].id}-{group[-1].id}",
                        "method": "POST",
                        "url": "/v1/responses",
                        "body": {
                            "model": settings.openai_model,
                            "input": self.pipeline.build_prompt(prompt_posts),
                        },
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(lines) + "\n"

    async def _apply_output(
        self,
        session: AsyncSession,
        job: AIBatchJob,
        content: str,
        user_id: int,
    ) -> int:
        result = await session.execute(select(Post).where(Post.ai_batch_job_id == job.id))
        posts = list(result.scalars())
        posts_by_id = {post.id: post for post in posts}
        ai_results = {}
        for line in content.splitlines():
            try:
                item = json.loads(line)
                response = item.get("response") or {}
                if response.get("status_code") != 200:
                    continue
                body = response.get("body") or {}
                text = self._response_output_text(body)
                ai_results.update(
                    self.pipeline.parse_results(
                        text,
                        [(post_id, post.raw_text) for post_id, post in posts_by_id.items()],
                        fallback_missing=False,
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue

        feedback = FeedbackRepository(session)
        category_affinity = await feedback.category_affinity(user_id)
        channel_affinity = await feedback.channel_affinity(user_id)
        existing = list(
            (
                await session.execute(
                    select(Post).where(Post.status.in_([PostStatus.processed, PostStatus.hidden]))
                )
            ).scalars()
        )
        scorer = RelevanceScorer()
        deduplicator = Deduplicator()
        applied = 0
        for post in posts:
            ai_result = ai_results.get(post.id)
            if not ai_result:
                post.ai_batch_job_id = None
                continue
            self._apply_ai_result(post, ai_result)
            post.duplicate_of_post_id = deduplicator.find_duplicate(post, existing)
            score, status, explanation = scorer.score(post, category_affinity, channel_affinity)
            post.relevance_score = score
            post.status = status
            post.explanation = explanation
            existing.append(post)
            applied += 1
        return applied

    async def _release_posts(self, session: AsyncSession, job_id: int) -> None:
        result = await session.execute(select(Post).where(Post.ai_batch_job_id == job_id))
        for post in result.scalars():
            post.ai_batch_job_id = None

    @staticmethod
    def _apply_ai_result(post: Post, result: Any) -> None:
        post.language = result.language
        post.summary = result.summary
        post.why_important = result.why_important
        post.category = result.category
        post.importance_score = result.importance_score
        post.relevance_score = result.relevance_score
        post.explanation = result.explanation
        post.is_promotional = result.is_promotional
        post.product_matches_json = serialize_product_matches(
            getattr(result, "product_matches", [])
        )

    @staticmethod
    def _response_output_text(body: dict[str, Any]) -> str:
        parts = []
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        return "".join(parts)
