"""Application settings, loaded from environment variables (and an optional .env file).

Every model ID, address and feature flag lives here so business logic never hard-codes them.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model APIs ---
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_main_model: str = "claude-sonnet-5-5"
    anthropic_extract_model: str = "claude-haiku-5-5"
    openai_judge_model: str | None = None
    openai_stt_model: str | None = None
    openai_embed_model: str | None = None

    # --- Database ---
    database_url: str | None = None

    # --- Temporal ---
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "recourse-cases"

    # --- Tracing ---
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None

    # --- Demo mode: shrinks 1 "day" to demo_day_seconds ---
    demo_mode: bool = False
    demo_day_seconds: int = Field(default=60, gt=0)

    # --- API ---
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached; call `get_settings.cache_clear()` in tests)."""
    return Settings()
