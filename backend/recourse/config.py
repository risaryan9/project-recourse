"""Application settings, loaded from environment variables (and an optional .env file).

Every model ID, address and feature flag lives here so business logic never hard-codes them.
"""

from functools import lru_cache
from typing import Literal

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
    anthropic_max_retries: int = Field(default=2, ge=0)  # SDK-level retries per call
    anthropic_timeout_seconds: float = Field(default=120.0, gt=0)
    # OpenAI as the fallback provider for the MAIN / EXTRACT roles (used when Anthropic fails).
    openai_main_model: str = "gpt-6.1-sol"
    openai_extract_model: str = "gpt-6.1-terra"
    openai_max_retries: int = Field(default=2, ge=0)
    openai_timeout_seconds: float = Field(default=120.0, gt=0)
    # Provider order for get_llm(); providers without an API key are skipped.
    llm_providers: list[Literal["anthropic", "openai"]] = ["anthropic", "openai"]

    # --- Database ---
    database_url: str | None = None
    test_database_url: str | None = None  # Neon "test" branch; used only by `pytest -m db`

    # --- Temporal ---
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "recourse-cases"

    # --- Bring your own key (ADR-023) ---
    # True: model calls for a case use only the key its owner gave in the UI, held in the API's
    # memory (never stored); the server's own keys are never used for visitors.
    require_user_keys: bool = False
    user_key_ttl_minutes: int = Field(default=360, gt=0)
    # The worker fetches a case's key from the API over loopback with this shared secret.
    internal_secret: SecretStr | None = None
    api_internal_url: str = "http://127.0.0.1:8000"

    # --- Tracing ---
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    # Agent addressing (recourse.tracing, ADR-024): traces go to this agent and environment.
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_agent_id: str | None = None
    langsmith_agent_environment: str = "local"
    langsmith_workspace_id: str | None = None

    # --- Demo mode: shrinks 1 "day" to demo_day_seconds ---
    demo_mode: bool = False
    demo_day_seconds: int = Field(default=60, gt=0)

    # --- Email ---
    # Sandbox (default on): every outgoing email goes to `sandbox_recipient`, never to a bank.
    email_sandbox: bool = True
    sandbox_recipient: str | None = None

    # --- Gmail (GmailChannel; CLAUDE.md §3 "Email channel") ---
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None  # must match the Google console exactly
    # Fernet key (urlsafe base64, 32 bytes) that encrypts Gmail refresh tokens at rest.
    token_encryption_key: SecretStr | None = None
    gmail_poll_minutes: int = Field(default=30, gt=0)
    demo_gmail_poll_seconds: int = Field(default=10, gt=0)

    # --- API ---
    # Where the API sends the browser back to after Google sign-in.
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]
    # Anonymous case ownership cookie (recourse.api.owner). Cross-site deployments need
    # samesite="none" and secure=True.
    owner_cookie_secure: bool = False
    owner_cookie_samesite: Literal["lax", "strict", "none"] = "lax"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached; call `get_settings.cache_clear()` in tests)."""
    return Settings()
