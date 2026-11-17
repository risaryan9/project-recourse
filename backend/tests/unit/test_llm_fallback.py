import pytest
from pydantic import BaseModel, SecretStr

from recourse.config import Settings
from recourse.llm import get_llm
from recourse.llm.base import (
    LLMOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMRequest,
    LLMUnavailableError,
    ModelRole,
)
from recourse.llm.fake import FakeLLM
from recourse.llm.fallback import FallbackLLM


class Label(BaseModel):
    label: str


REQ = LLMRequest(purpose="reply.classify", role=ModelRole.EXTRACT, system="s", task="t")


def two(primary: FakeLLM, secondary: FakeLLM) -> FallbackLLM:
    return FallbackLLM([("primary", primary), ("secondary", secondary)])


async def test_primary_success_does_not_touch_secondary() -> None:
    primary = FakeLLM({"reply.classify": [{"label": "a"}]})
    secondary = FakeLLM()
    result = await two(primary, secondary).structured(REQ, Label)
    assert result.output == Label(label="a")
    assert secondary.calls == []


@pytest.mark.parametrize(
    "failure", [LLMUnavailableError("overloaded"), LLMRejectedError("401 bad key")]
)
async def test_falls_back_on_provider_failures(failure: Exception) -> None:
    primary = FakeLLM({"reply.classify": [failure]})
    secondary = FakeLLM({"reply.classify": ["from secondary"]})
    result = await two(primary, secondary).text(REQ)
    assert result.output == "from secondary"
    assert len(secondary.calls) == 1


@pytest.mark.parametrize(
    "failure", [LLMRefusalError("declined", "cyber"), LLMOutputError("truncated")]
)
async def test_does_not_fall_back_on_refusal_or_bad_output(failure: Exception) -> None:
    primary = FakeLLM({"reply.classify": [failure]})
    secondary = FakeLLM({"reply.classify": ["should not be used"]})
    with pytest.raises(type(failure)):
        await two(primary, secondary).text(REQ)
    assert secondary.calls == []


async def test_all_fail_with_one_retryable_is_retryable() -> None:
    # Anthropic overloaded, OpenAI misconfigured: retrying later can still succeed.
    primary = FakeLLM({"reply.classify": [LLMUnavailableError("overloaded")]})
    secondary = FakeLLM({"reply.classify": [LLMRejectedError("401")]})
    with pytest.raises(LLMUnavailableError) as exc:
        await two(primary, secondary).text(REQ)
    assert exc.value.retryable


async def test_all_fail_non_retryable() -> None:
    primary = FakeLLM({"reply.classify": [LLMRejectedError("401")]})
    secondary = FakeLLM({"reply.classify": [LLMRejectedError("401")]})
    with pytest.raises(LLMRejectedError):
        await two(primary, secondary).text(REQ)


async def test_failure_log_has_no_content(caplog: pytest.LogCaptureFixture) -> None:
    primary = FakeLLM({"reply.classify": [LLMUnavailableError("down")]})
    secondary = FakeLLM({"reply.classify": ["ok"]})
    await two(primary, secondary).text(REQ)
    assert "provider=primary" in caplog.text
    assert "error=LLMUnavailableError" in caplog.text


def test_needs_a_provider() -> None:
    with pytest.raises(ValueError):
        FallbackLLM([])


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_get_llm_uses_keys_in_order() -> None:
    llm = get_llm(settings(anthropic_api_key=SecretStr("a"), openai_api_key=SecretStr("o")))
    assert isinstance(llm, FallbackLLM)
    assert llm.provider_names == ["anthropic", "openai"]


def test_get_llm_skips_missing_and_empty_keys() -> None:
    llm = get_llm(settings(anthropic_api_key=SecretStr(""), openai_api_key=SecretStr("o")))
    assert isinstance(llm, FallbackLLM)
    assert llm.provider_names == ["openai"]


def test_get_llm_respects_provider_order() -> None:
    llm = get_llm(
        settings(
            anthropic_api_key=SecretStr("a"),
            openai_api_key=SecretStr("o"),
            llm_providers=["openai", "anthropic"],
        )
    )
    assert isinstance(llm, FallbackLLM)
    assert llm.provider_names == ["openai", "anthropic"]


def test_get_llm_without_keys_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="No LLM provider"):
        get_llm(settings())
