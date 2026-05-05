from functools import cached_property

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(default="sqlite+aiosqlite:///./data/app.db", alias="DATABASE_URL")
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL")
    telegram_api_id: int | None = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    telegram_session_name: str = Field(default="tg_summariser", alias="TELEGRAM_SESSION_NAME")
    telegram_session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    owner_telegram_id: int | None = Field(default=None, alias="OWNER_TELEGRAM_ID")
    digest_schedules: str = Field(default="09:00,14:00,19:00", alias="DIGEST_SCHEDULES")
    timezone: str = Field(default="Europe/Lisbon", alias="TIMEZONE")

    @cached_property
    def digest_times(self) -> list[str]:
        return [item.strip() for item in self.digest_schedules.split(",") if item.strip()]


settings = Settings()
