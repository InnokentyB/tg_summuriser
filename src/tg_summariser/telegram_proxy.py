from urllib.parse import unquote, urlparse


def telethon_proxy_from_url(url: str) -> dict[str, object] | None:
    """Convert TELEGRAM_PROXY_URL into Telethon's explicit proxy mapping."""
    value = url.strip()
    if not value:
        return None

    parsed = urlparse(value)
    if parsed.scheme not in {"socks4", "socks5", "http"}:
        raise ValueError("TELEGRAM_PROXY_URL must use socks4, socks5, or http")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("TELEGRAM_PROXY_URL must include a host and port")

    proxy: dict[str, object] = {
        "proxy_type": parsed.scheme,
        "addr": parsed.hostname,
        "port": parsed.port,
        "rdns": True,
    }
    if parsed.username is not None:
        proxy["username"] = unquote(parsed.username)
    if parsed.password is not None:
        proxy["password"] = unquote(parsed.password)
    return proxy
