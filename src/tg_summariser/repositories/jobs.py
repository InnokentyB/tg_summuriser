from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_summariser.models import ChannelOnboardingJob


class ChannelOnboardingJobRepository:
    """Persistence operations for channel onboarding background queue jobs."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with active database session."""
        self.session = session

    async def enqueue(self, channel_id: int, telegram_user_id: int) -> tuple[ChannelOnboardingJob, bool]:
        """Enqueue or reset an onboarding task for a channel."""
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.channel_id == channel_id)
            .order_by(ChannelOnboardingJob.id.asc())
        )
        job = result.scalars().first()
        if job:
            was_already_waiting = job.status in {"pending", "processing"}
            job.telegram_user_id = telegram_user_id
            job.status = "pending"
            job.updated_at = datetime.utcnow()
            job.completed_at = None
            job.last_error = None
            return job, not was_already_waiting

        job = ChannelOnboardingJob(
            channel_id=channel_id,
            telegram_user_id=telegram_user_id,
            status="pending",
        )
        self.session.add(job)
        await self.session.flush()
        return job, True

    async def recoverable_jobs(self) -> list[ChannelOnboardingJob]:
        """Fetch pending or stuck processing jobs eligible for worker resumption."""
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.status.in_(["pending", "processing"]))
            .order_by(ChannelOnboardingJob.updated_at.asc())
        )
        return list(result.scalars())

    async def failed_jobs(self) -> list[ChannelOnboardingJob]:
        """Fetch failed jobs with loaded channel relations."""
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .options(selectinload(ChannelOnboardingJob.channel))
            .where(ChannelOnboardingJob.status == "failed")
            .order_by(ChannelOnboardingJob.updated_at.asc())
        )
        return list(result.scalars())

    async def mark_processing(self, channel_id: int) -> None:
        """Mark a job as currently processing and increment attempt count."""
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "processing"
        job.attempts += 1
        job.updated_at = datetime.utcnow()

    async def mark_completed(self, channel_id: int) -> None:
        """Mark an onboarding job as successfully finished."""
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        job.last_error = None

    async def mark_failed(self, channel_id: int, error: str) -> None:
        """Mark job as failed and record diagnostic error message."""
        job = await self._get_by_channel_id(channel_id)
        if not job:
            return
        job.status = "failed"
        job.last_error = error[:1000]
        job.updated_at = datetime.utcnow()

    async def _get_by_channel_id(self, channel_id: int) -> ChannelOnboardingJob | None:
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .where(ChannelOnboardingJob.channel_id == channel_id)
            .order_by(ChannelOnboardingJob.id.asc())
        )
        return result.scalars().first()

    async def status_counts(self) -> dict[str, int]:
        """Return aggregation of job counts grouped by status."""
        result = await self.session.execute(
            select(ChannelOnboardingJob.status, func.count(ChannelOnboardingJob.id)).group_by(
                ChannelOnboardingJob.status
            )
        )
        return {status: int(count) for status, count in result.all()}

    async def recent_jobs(self, limit: int = 10) -> list[ChannelOnboardingJob]:
        """Return the most recently updated onboarding jobs."""
        result = await self.session.execute(
            select(ChannelOnboardingJob)
            .options(selectinload(ChannelOnboardingJob.channel))
            .order_by(ChannelOnboardingJob.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars())
