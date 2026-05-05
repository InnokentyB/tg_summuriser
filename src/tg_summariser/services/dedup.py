from __future__ import annotations

from difflib import SequenceMatcher

from tg_summariser.models import Post


class Deduplicator:
    def find_duplicate(self, post: Post, existing_posts: list[Post]) -> int | None:
        for candidate in existing_posts:
            if candidate.id == post.id:
                continue
            ratio = SequenceMatcher(None, post.normalized_text, candidate.normalized_text).ratio()
            if ratio >= 0.92:
                return candidate.id
        return None

