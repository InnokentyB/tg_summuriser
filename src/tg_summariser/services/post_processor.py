from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.config import settings
from tg_summariser.models import Post, PostStatus
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.prefilter import LocalPrefilter
from tg_summariser.services.repositories import FeedbackRepository, PostRepository
from tg_summariser.services.scoring import RelevanceScorer


class PostProcessor:
    def __init__(
        self,
        ai_pipeline: AIPipeline,
        deduplicator: Deduplicator,
        scorer: RelevanceScorer,
        prefilter: LocalPrefilter | None = None,
    ):
        self.ai_pipeline = ai_pipeline
        self.deduplicator = deduplicator
        self.scorer = scorer
        self.prefilter = prefilter or LocalPrefilter()

    async def process_pending(self, session: AsyncSession, user_id: int) -> int:
        post_repo = PostRepository(session)
        feedback_repo = FeedbackRepository(session)
        posts = await post_repo.pending_posts(limit=settings.ai_processing_limit_per_run)
        if not posts:
            return 0

        category_affinity = await feedback_repo.category_affinity(user_id)
        channel_affinity = await feedback_repo.channel_affinity(user_id)
        existing_posts = list(
            (
                await session.execute(
                    select(Post).where(Post.status.in_([PostStatus.processed, PostStatus.hidden]))
                )
            ).scalars()
        )

        processed = 0
        for post in posts:
            prefilter_decision = self.prefilter.decide(post, channel_affinity=channel_affinity)
            ai_result = prefilter_decision.ai_result
            if prefilter_decision.should_call_ai:
                ai_result = await self.ai_pipeline.process_post(post.raw_text)
            if ai_result is None:
                continue
            post.language = ai_result.language
            post.summary = ai_result.summary
            post.why_important = ai_result.why_important
            post.category = ai_result.category
            post.importance_score = ai_result.importance_score
            post.relevance_score = ai_result.relevance_score
            post.explanation = ai_result.explanation
            post.duplicate_of_post_id = self.deduplicator.find_duplicate(post, existing_posts)
            if prefilter_decision.forced_status:
                post.status = prefilter_decision.forced_status
                post.explanation = prefilter_decision.explanation or post.explanation
            else:
                score, status, explanation = self.scorer.score(post, category_affinity, channel_affinity)
                post.relevance_score = score
                post.status = status
                post.explanation = explanation
            existing_posts.append(post)
            processed += 1
        return processed
