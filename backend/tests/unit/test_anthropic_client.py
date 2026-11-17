"""AnthropicLLM against a mock HTTP transport: real SDK, real request/response handling,
no network and no API key."""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from recourse.config import Settings
from recourse.llm.anthropic_client import FALLBACK_BETA, AnthropicLLM
from recourse.llm.base import (
    LLMClient,
    LLMOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMRequest,
    LLMUnavailableError,
    ModelRole,
    UntrustedDocument,
)

SETTINGS = Settings(
    anthropic_main_model="main-model-id",
    anthropic_extract_model="extract-model-id",
    _env_file=None,
)


class Label(BaseModel):
    label: str
    confidence: float


def message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    model: str = "extract-model-id",
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "stop_details": stop_details,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "cache_read_input_tokens": 600,
            "cache_creation_input_tokens": 0,
        },
    }


class Recorder:
    """A mock transport that records request bodies and replies with a fixed response."""

    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.respond = respond
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        return self.respond(request)


def make_llm(respond: Callable[[httpx2.Request], httpx2.Response]) -> tuple[AnthropicLLM, Recorder]:
    recorder = Recorder(respond)
    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
    )
    return AnthropicLLM(SETTINGS, client=client), recorder


def ok(body: dict[str, Any]) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(200, json=body)


def req(**kw: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "purpose": "reply.classify",
        "role": ModelRole.EXTRACT,
        "system": "Classify the bank message.",
        "task": "Return one label.",
        "documents": [UntrustedDocument(kind="bank_reply", text="Complaint registered")],
    }
    base.update(kw)
    return LLMRequest.model_validate(base)


def test_satisfies_the_interface() -> None:
    llm: LLMClient = AnthropicLLM(SETTINGS, client=anthropic.AsyncAnthropic(api_key="x"))
    assert llm is not None


async def test_structured_success_and_request_shape() -> None:
    answer = {"label": "acknowledgement", "confidence": 0.93}
    llm, rec = make_llm(ok(message([{"type": "text", "text": json.dumps(answer)}])))

    result = await llm.structured(req(effort="low"), Label)

    assert result.output == Label(label="acknowledgement", confidence=0.93)
    assert result.served_model == "extract-model-id"
    assert result.usage.input_tokens == 120
    assert result.usage.cache_read_input_tokens == 600

    [body] = rec.bodies
    assert body["model"] == "extract-model-id"
    assert body["max_tokens"] == 16_000
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert set(fmt["schema"]["properties"]) == {"label", "confidence"}
    assert body["output_config"]["effort"] == "low"
    assert "fallbacks" not in body
    assert "Complaint registered" in body["messages"][0]["content"][-1]["text"]


async def test_main_role_and_fallback() -> None:
    llm, rec = make_llm(ok(message([{"type": "text", "text": "Dear Sir"}], model="main-model-id")))
    result = await llm.text(
        req(purpose="draft.complaint", role=ModelRole.MAIN, allow_fallback=True)
    )
    assert result.output == "Dear Sir"
    [body] = rec.bodies
    assert body["model"] == "main-model-id"
    assert body["fallbacks"] == "default"
    assert "output_config" not in body
    assert FALLBACK_BETA in rec.headers[0]["anthropic-beta"]


async def test_served_model_reports_a_fallback() -> None:
    llm, _ = make_llm(ok(message([{"type": "text", "text": "Dear Sir"}], model="other-model")))
    result = await llm.text(req(role=ModelRole.MAIN, allow_fallback=True))
    assert result.served_model == "other-model"


async def test_thinking_blocks_are_skipped() -> None:
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Hello"},
    ]
    llm, _ = make_llm(ok(message(content)))
    assert (await llm.text(req())).output == "Hello"


async def test_refusal_is_reported_with_category() -> None:
    body = message(
        [],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber", "explanation": None},
    )
    llm, _ = make_llm(ok(body))
    with pytest.raises(LLMRefusalError) as exc:
        await llm.structured(req(), Label)
    assert exc.value.category == "cyber"
    assert not exc.value.retryable


async def test_truncated_output() -> None:
    llm, _ = make_llm(
        ok(message([{"type": "text", "text": '{"label": "ac'}], stop_reason="max_tokens"))
    )
    with pytest.raises(LLMOutputError, match="truncated"):
        await llm.structured(req(), Label)


async def test_schema_mismatch_does_not_leak_values() -> None:
    bad = {"label": "user secret text", "confidence": "high"}
    llm, _ = make_llm(ok(message([{"type": "text", "text": json.dumps(bad)}])))
    with pytest.raises(LLMOutputError) as exc:
        await llm.structured(req(), Label)
    assert "confidence" in str(exc.value)
    assert "user secret text" not in str(exc.value)


async def test_empty_text_is_an_error() -> None:
    llm, _ = make_llm(ok(message([])))
    with pytest.raises(LLMOutputError):
        await llm.text(req())


def error(status: int, kind: str) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"type": "error", "error": {"type": kind, "message": "boom"}}
    return lambda _: httpx2.Response(status, json=body)


@pytest.mark.parametrize(
    ("status", "kind", "expected"),
    [
        (429, "rate_limit_error", LLMUnavailableError),
        (500, "api_error", LLMUnavailableError),
        (529, "overloaded_error", LLMUnavailableError),
        (400, "invalid_request_error", LLMRejectedError),
        (401, "authentication_error", LLMRejectedError),
        (404, "not_found_error", LLMRejectedError),
    ],
)
async def test_api_errors_mapped(status: int, kind: str, expected: type[Exception]) -> None:
    llm, _ = make_llm(error(status, kind))
    with pytest.raises(expected) as exc:
        await llm.text(req())
    assert exc.value.retryable is (expected is LLMUnavailableError)  # type: ignore[attr-defined]


async def test_connection_error_is_retryable() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    llm, _ = make_llm(fail)
    with pytest.raises(LLMUnavailableError):
        await llm.text(req())


async def test_logs_do_not_contain_prompt_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="recourse.llm.anthropic_client")
    llm, _ = make_llm(ok(message([{"type": "text", "text": "secret answer"}])))
    await llm.text(req(documents=[UntrustedDocument(kind="sms", text="private sms body")]))
    logged = caplog.text
    assert "purpose=reply.classify" in logged
    assert "private sms body" not in logged
    assert "secret answer" not in logged
