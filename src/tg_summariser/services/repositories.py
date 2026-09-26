"""Facade module maintaining 100% backward compatibility for repository imports.

All domain repositories have been decomposed into `tg_summariser.repositories`.
"""

from __future__ import annotations

from tg_summariser.repositories import (
    ChannelOnboardingJobRepository,
    ChannelRepository,
    DigestRepository,
    FeedbackRepository,
    PostReactionStat,
    PostRepository,
    UserCategoryPreferenceRepository,
    UserRepository,
    normalize_telegram_chat_id,
)

__all__ = [
    "ChannelOnboardingJobRepository",
    "ChannelRepository",
    "DigestRepository",
    "FeedbackRepository",
    "PostReactionStat",
    "PostRepository",
    "UserCategoryPreferenceRepository",
    "UserRepository",
    "normalize_telegram_chat_id",
]
