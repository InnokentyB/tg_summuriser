from tg_summariser.config import settings


def test_settings_has_digest_times() -> None:
    assert settings.digest_times
