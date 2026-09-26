from __future__ import annotations

from tg_summariser.repositories.categories import UserCategoryPreferenceRepository
from tg_summariser.repositories.channels import ChannelRepository
from tg_summariser.repositories.digest import DigestRepository
from tg_summariser.repositories.feedback import FeedbackRepository, PostReactionStat
from tg_summariser.repositories.jobs import ChannelOnboardingJobRepository
from tg_summariser.repositories.posts import PostRepository
from tg_summariser.repositories.users import UserRepository
from tg_summariser.repositories.utils import normalize_telegram_chat_id

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
