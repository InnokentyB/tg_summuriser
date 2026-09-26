from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tg_summariser.models import Post

_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_PARAMS = {"fbclid", "gclid", "yclid"}
_TELEGRAM_HOSTS = {"t.me", "telegram.me", "telegram.dog"}
_NON_VENDOR_DOMAINS = {"arxiv.org"}
_KNOWN_VENDOR_PATTERNS = (
    ("visure", re.compile(r"\bvisure(?:\s+solutions)?\b", re.IGNORECASE)),
    ("netflix", re.compile(r"\bnetflix\b", re.IGNORECASE)),
    ("gitlab", re.compile(r"\bgitlab\b", re.IGNORECASE)),
    ("aws", re.compile(r"\baws\b|\bamazon\s+web\s+services\b", re.IGNORECASE)),
    ("langchain", re.compile(r"\blangchain\b", re.IGNORECASE)),
    ("openai", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("anthropic", re.compile(r"\banthropic\b|\bclaude\b", re.IGNORECASE)),
)


def normalize_telegram_chat_id(chat_id: int) -> int:
    """Normalize Telegram channel/chat IDs by stripping negative prefixes."""
    raw = str(chat_id)
    if raw.startswith("-100"):
        return int(raw[4:])
    if raw.startswith("-"):
        return int(raw[1:])
    return chat_id


def canonical_url(raw_url: str) -> str | None:
    """Normalize and clean URL by removing tracking query parameters."""
    cleaned = raw_url.rstrip(".,;:!?)]}»")
    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return None
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
        and key.lower() not in _TRACKING_QUERY_PARAMS
    ]
    path = parsed.path.rstrip("/")
    normalized = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(filtered_query),
            "",
        )
    )
    return normalized


def registrable_domain(host: str) -> str:
    """Extract registered domain name from host string."""
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def external_urls(post: Post) -> list[str]:
    """Extract distinct non-Telegram canonical external URLs from post texts."""
    values = [post.original_link, post.raw_text, post.normalized_text, post.summary, post.why_important]
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for raw_url in _URL_RE.findall(value):
            url = canonical_url(raw_url)
            if not url:
                continue
            host = urlsplit(url).hostname or ""
            if host in _TELEGRAM_HOSTS or host.endswith(".telegram.org"):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls


def article_key(post: Post) -> str | None:
    """Return deduplication key for external article URLs in a post."""
    for url in external_urls(post):
        return f"url:{url}"
    return None


def vendor_key(post: Post) -> str | None:
    """Extract vendor identifier or domain key to avoid repetitive vendor digests."""
    text = " ".join(
        value
        for value in (post.summary, post.raw_text, post.normalized_text, post.why_important)
        if value
    )
    for v_key, pattern in _KNOWN_VENDOR_PATTERNS:
        if pattern.search(text):
            return f"vendor:{v_key}"

    for url in external_urls(post):
        host = urlsplit(url).hostname or ""
        if host:
            domain = registrable_domain(host)
            if domain not in _NON_VENDOR_DOMAINS:
                return f"domain:{domain}"
    return None
