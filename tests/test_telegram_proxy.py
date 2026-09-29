import pytest

from tg_summariser.telegram_proxy import telethon_proxy_from_url


def test_telethon_proxy_from_socks5_url() -> None:
    assert telethon_proxy_from_url("socks5://proxy.internal:40000") == {
        "proxy_type": "socks5",
        "addr": "proxy.internal",
        "port": 40000,
        "rdns": True,
    }


def test_telethon_proxy_preserves_credentials() -> None:
    assert telethon_proxy_from_url("socks5://user:pass@proxy.internal:1080") == {
        "proxy_type": "socks5",
        "addr": "proxy.internal",
        "port": 1080,
        "username": "user",
        "password": "pass",
        "rdns": True,
    }


def test_telethon_proxy_returns_none_when_unconfigured() -> None:
    assert telethon_proxy_from_url("") is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://proxy.internal:21",
        "socks5://proxy.internal",
        "socks5://:40000",
    ],
)
def test_telethon_proxy_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError, match="TELEGRAM_PROXY_URL"):
        telethon_proxy_from_url(url)
