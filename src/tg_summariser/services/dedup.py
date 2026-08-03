from __future__ import annotations

import re
from difflib import SequenceMatcher

from tg_summariser.models import Post


_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.IGNORECASE)

_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "from",
    "has",
    "have",
    "into",
    "new",
    "not",
    "that",
    "the",
    "this",
    "was",
    "will",
    "with",
    "без",
    "более",
    "был",
    "была",
    "были",
    "было",
    "для",
    "его",
    "еще",
    "или",
    "как",
    "компания",
    "который",
    "над",
    "новый",
    "новая",
    "новое",
    "новые",
    "она",
    "они",
    "под",
    "подробнее",
    "после",
    "при",
    "про",
    "свой",
    "свои",
    "свою",
    "также",
    "уже",
    "что",
    "это",
}


class Deduplicator:
    def find_duplicate(self, post: Post, existing_posts: list[Post]) -> int | None:
        for candidate in existing_posts:
            if candidate.id == post.id:
                continue
            if self._is_duplicate(post, candidate):
                return candidate.id
        return None

    def _is_duplicate(self, post: Post, candidate: Post) -> bool:
        for current_text in self._comparison_texts(post):
            for candidate_text in self._comparison_texts(candidate):
                if self._text_similarity(current_text, candidate_text) >= 0.92:
                    return True
                if self._token_similarity(current_text, candidate_text):
                    return True
        return False

    def _comparison_texts(self, post: Post) -> list[str]:
        values = [post.normalized_text, post.summary or "", post.why_important or ""]
        return [value for value in values if value.strip()]

    def _text_similarity(self, left: str, right: str) -> float:
        return SequenceMatcher(None, self._normalize_text(left), self._normalize_text(right)).ratio()

    def _token_similarity(self, left: str, right: str) -> bool:
        left_tokens = self._significant_tokens(left)
        right_tokens = self._significant_tokens(right)
        if len(left_tokens) < 5 or len(right_tokens) < 5:
            return False

        intersection = left_tokens & right_tokens
        jaccard = len(intersection) / len(left_tokens | right_tokens)
        containment = len(intersection) / min(len(left_tokens), len(right_tokens))
        if jaccard >= 0.58 or containment >= 0.72:
            return True
        return len(intersection) >= 4 and containment >= 0.5 and self._has_distinctive_overlap(
            intersection
        )

    def _normalize_text(self, text: str) -> str:
        without_urls = _URL_RE.sub(" ", text.casefold())
        return " ".join(_TOKEN_RE.findall(without_urls))

    def _significant_tokens(self, text: str) -> set[str]:
        tokens = set()
        for raw_token in _TOKEN_RE.findall(_URL_RE.sub(" ", text.casefold())):
            token = self._stem_token(raw_token)
            if len(token) < 3 or token in _STOPWORDS:
                continue
            tokens.add(token)
        return tokens

    def _stem_token(self, token: str) -> str:
        if token.startswith("агент"):
            return "агент"
        for suffix in (
            "иями",
            "ями",
            "ами",
            "ого",
            "ему",
            "ыми",
            "ими",
            "ской",
            "ский",
            "ских",
            "ная",
            "ное",
            "ные",
            "ого",
            "ую",
            "ых",
            "ий",
            "ый",
            "ой",
            "ая",
            "ое",
            "ые",
            "ing",
            "ers",
            "ies",
            "ed",
            "es",
            "s",
        ):
            if len(token) - len(suffix) >= 4 and token.endswith(suffix):
                return token[: -len(suffix)]
        return token

    def _has_distinctive_overlap(self, tokens: set[str]) -> bool:
        return any(any(ch.isdigit() for ch in token) or token.isascii() for token in tokens)
