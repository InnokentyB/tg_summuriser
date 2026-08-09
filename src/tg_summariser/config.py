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
    ai_processing_limit_per_run: int = Field(default=20, alias="AI_PROCESSING_LIMIT_PER_RUN")
    ai_min_text_length: int = Field(default=120, alias="AI_MIN_TEXT_LENGTH")
    ai_max_input_chars: int = Field(default=4000, alias="AI_MAX_INPUT_CHARS")
    ai_prefilter_enabled: bool = Field(default=True, alias="AI_PREFILTER_ENABLED")
    ai_prefilter_strict: bool = Field(default=False, alias="AI_PREFILTER_STRICT")
    ai_prefilter_positive_keywords: str = Field(
        default=(
            "ai,ии,llm,gpt,openai,anthropic,claude,agent,agents,агент,агенты,"
            "бизнес,startup,стартап,saas,инвестиции,product,продукт"
        ),
        alias="AI_PREFILTER_POSITIVE_KEYWORDS",
    )
    ai_prefilter_negative_keywords: str = Field(
        default=(
            "реклама,промо,скидка,подписывайтесь,подписаться,курс,вебинар,"
            "розыгрыш,донат,вакансия,нанимаем"
        ),
        alias="AI_PREFILTER_NEGATIVE_KEYWORDS",
    )
    telegram_api_id: int | None = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    telegram_session_name: str = Field(default="tg_summariser", alias="TELEGRAM_SESSION_NAME")
    telegram_session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    telegram_sync_channel_limit: int = Field(default=5, alias="TELEGRAM_SYNC_CHANNEL_LIMIT")
    telegram_sync_min_interval_minutes: int = Field(
        default=360,
        alias="TELEGRAM_SYNC_MIN_INTERVAL_MINUTES",
    )
    telegram_sync_delay_seconds: float = Field(default=8.0, alias="TELEGRAM_SYNC_DELAY_SECONDS")
    owner_telegram_id: int | None = Field(default=None, alias="OWNER_TELEGRAM_ID")
    digest_schedules: str = Field(default="09:00,14:00,19:00", alias="DIGEST_SCHEDULES")
    timezone: str = Field(default="Europe/Lisbon", alias="TIMEZONE")
    tgarticles_database_url: str = Field(default="", alias="TGARTICLES_DATABASE_URL")
    tgarticles_import_enabled: bool = Field(default=True, alias="TGARTICLES_IMPORT_ENABLED")
    tgarticles_import_days: int = Field(default=3, alias="TGARTICLES_IMPORT_DAYS")
    tgarticles_import_limit: int = Field(default=50, alias="TGARTICLES_IMPORT_LIMIT")
    tgarticles_min_text_length: int = Field(default=900, alias="TGARTICLES_MIN_TEXT_LENGTH")
    tgarticles_source_chat_id: int = Field(default=910000001, alias="TGARTICLES_SOURCE_CHAT_ID")
    tgarticles_import_schedules: str = Field(
        default="08:30,11:30,14:30,17:30,20:30",
        alias="TGARTICLES_IMPORT_SCHEDULES",
    )

    @cached_property
    def digest_times(self) -> list[str]:
        return [item.strip() for item in self.digest_schedules.split(",") if item.strip()]

    @cached_property
    def tgarticles_import_times(self) -> list[str]:
        return [item.strip() for item in self.tgarticles_import_schedules.split(",") if item.strip()]

    @cached_property
    def normalized_database_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
