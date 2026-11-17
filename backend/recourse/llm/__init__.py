"""LLM access: one interface (`LLMClient`), a real client and a fake. See `base.py`."""

from pydantic import SecretStr

from recourse.config import Settings, get_settings
from recourse.llm.base import (
    Attachment,
    LLMClient,
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMRequest,
    LLMResult,
    LLMUnavailableError,
    LLMUsage,
    ModelRole,
    UntrustedDocument,
)

__all__ = [
    "Attachment",
    "LLMClient",
    "LLMError",
    "LLMOutputError",
    "LLMRefusalError",
    "LLMRejectedError",
    "LLMRequest",
    "LLMResult",
    "LLMUnavailableError",
    "LLMUsage",
    "ModelRole",
    "UntrustedDocument",
    "get_llm",
]


def get_llm(settings: Settings | None = None) -> LLMClient:
    """The production client: providers in `settings.llm_providers` order, each used only if its
    API key is set, wrapped in `FallbackLLM`. Tests construct `FakeLLM` directly instead."""
    from recourse.llm.anthropic_client import AnthropicLLM
    from recourse.llm.fallback import FallbackLLM
    from recourse.llm.openai_client import OpenAILLM

    settings = settings or get_settings()
    providers: list[tuple[str, LLMClient]] = []
    for name in settings.llm_providers:
        if name == "anthropic" and _has_key(settings.anthropic_api_key):
            providers.append((name, AnthropicLLM(settings)))
        elif name == "openai" and _has_key(settings.openai_api_key):
            providers.append((name, OpenAILLM(settings)))
    if not providers:
        raise RuntimeError("No LLM provider configured: set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    return FallbackLLM(providers)


def _has_key(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())
