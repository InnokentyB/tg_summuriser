from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_summariser.config import settings
from tg_summariser.models import Post, PostStatus
from tg_summariser.services.ai_pipeline import AIPipeline
from tg_summariser.services.dedup import Deduplicator
from tg_summariser.services.prefilter import LocalPrefilter
from tg_summariser.services.product_radar import serialize_product_matches
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
        await post_repo.hide_stale_pending(settings.digest_max_post_age_days)
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

        decisions = {
            post.id: self.prefilter.decide(post, channel_affinity=channel_affinity)
            for post in posts
        }
        ai_posts = [
            (post.id, post.raw_text)
            for post in posts
            if decisions[post.id].should_call_ai
        ]
        ai_results = await self.ai_pipeline.process_posts(ai_posts) if ai_posts else {}

        processed = 0
        for post in posts:
            prefilter_decision = decisions[post.id]
            ai_result = ai_results.get(post.id, prefilter_decision.ai_result)
            if ai_result is None:
                continue
            post.language = ai_result.language
            post.summary = ai_result.summary
            post.why_important = ai_result.why_important
            post.category = ai_result.category
            post.importance_score = ai_result.importance_score
            post.relevance_score = ai_result.relevance_score
            post.explanation = ai_result.explanation
            post.is_promotional = getattr(ai_result, "is_promotional", False)
            post.product_matches_json = serialize_product_matches(
                getattr(ai_result, "product_matches", [])
            )
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
