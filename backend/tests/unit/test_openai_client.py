"""OpenAILLM against a mock HTTP transport: real SDK, real request/response handling,
no network and no API key."""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel

from recourse.config import Settings
from recourse.llm.base import (
    Attachment,
    LLMClient,
    LLMOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMRequest,
    LLMUnavailableError,
    ModelRole,
    UntrustedDocument,
)
from recourse.llm.openai_client import OpenAILLM
from recourse.llm.prompting import SAFETY_PREAMBLE, UNTRUSTED_TAG

SETTINGS = Settings(
    openai_main_model="oa-main",
    openai_extract_model="oa-extract",
    _env_file=None,
)


class Label(BaseModel):
    label: str
    confidence: float
    note: str | None = None


def response(
    output: list[dict[str, Any]],
    status: str = "completed",
    model: str = "oa-extract",
    incomplete: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1_700_000_000,
        "model": model,
        "status": status,
        "incomplete_details": incomplete,
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 200,
            "input_tokens_details": {"cached_tokens": 150},
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 240,
        },
    }


def message(*parts: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": list(parts),
    }


def out_text(text: str) -> dict[str, Any]:
    return {"type": "output_text", "text": text, "annotations": []}


class Recorder:
    def __init__(self, respond: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.respond = respond
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self.respond(request)


def make_llm(respond: Callable[[httpx2.Request], httpx2.Response]) -> tuple[OpenAILLM, Recorder]:
    recorder = Recorder(respond)
    client = openai.AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
    )
    return OpenAILLM(SETTINGS, client=client), recorder


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
    llm: LLMClient = OpenAILLM(SETTINGS, client=openai.AsyncOpenAI(api_key="x"))
    assert llm is not None


async def test_structured_success_and_request_shape() -> None:
    answer = {"label": "acknowledgement", "confidence": 0.9, "note": None}
    llm, rec = make_llm(ok(response([message(out_text(json.dumps(answer)))])))

    result = await llm.structured(req(effort="low"), Label)

    assert result.output == Label(label="acknowledgement", confidence=0.9)
    assert result.served_model == "oa-extract"
    assert result.usage.input_tokens == 200
    assert result.usage.cache_read_input_tokens == 150

    [body] = rec.bodies
    assert body["model"] == "oa-extract"
    assert body["store"] is False
    assert body["max_output_tokens"] == 16_000
    assert body["reasoning"] == {"effort": "low"}
    assert body["instructions"].startswith(SAFETY_PREAMBLE)
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "Label"
    # Strict mode: every property required, no extras.
    assert set(fmt["schema"]["required"]) == {"label", "confidence", "note"}
    assert fmt["schema"]["additionalProperties"] is False
    [user] = body["input"]
    [text_part] = user["content"]
    assert text_part["type"] == "input_text"
    assert f'<{UNTRUSTED_TAG} index="1" kind="bank_reply">' in text_part["text"]
    assert text_part["text"].rstrip().endswith("</task>")


async def test_main_role_and_text() -> None:
    llm, rec = make_llm(ok(response([message(out_text("Dear Sir"))], model="oa-main")))
    result = await llm.text(req(role=ModelRole.MAIN, allow_fallback=True))
    assert result.output == "Dear Sir"
    [body] = rec.bodies
    assert body["model"] == "oa-main"
    assert "text" not in body and "reasoning" not in body


async def test_attachments_become_image_and_file_parts() -> None:
    png, pdf = b"\x89PNG fake", b"%PDF-1.7 fake"
    llm, rec = make_llm(ok(response([message(out_text("ok"))])))
    await llm.text(
        req(
            attachments=[
                Attachment(media_type="image/png", data=png),
                Attachment(media_type="application/pdf", data=pdf),
            ]
        )
    )
    image, file, text = rec.bodies[0]["input"][0]["content"]
    assert image == {
        "type": "input_image",
        "image_url": "data:image/png;base64," + base64.standard_b64encode(png).decode(),
        "detail": "high",
    }
    assert file["type"] == "input_file"
    assert (
        file["file_data"]
        == "data:application/pdf;base64," + base64.standard_b64encode(pdf).decode()
    )
    assert file["filename"].endswith(".pdf")
    assert text["type"] == "input_text"


async def test_pii_redacted_before_sending() -> None:
    llm, rec = make_llm(ok(response([message(out_text("ok"))])))
    await llm.text(req(documents=[UntrustedDocument(kind="sms", text="OTP is 482913")]))
    assert "482913" not in json.dumps(rec.bodies[0])


async def test_refusal() -> None:
    body = response([message({"type": "refusal", "refusal": "I can't help with that."})])
    llm, _ = make_llm(ok(body))
    with pytest.raises(LLMRefusalError):
        await llm.structured(req(), Label)


async def test_content_filter_is_a_refusal() -> None:
    body = response([], status="incomplete", incomplete={"reason": "content_filter"})
    llm, _ = make_llm(ok(body))
    with pytest.raises(LLMRefusalError):
        await llm.text(req())


async def test_truncated_output() -> None:
    body = response(
        [message(out_text('{"label": "ac'))],
        status="incomplete",
        incomplete={"reason": "max_output_tokens"},
    )
    llm, _ = make_llm(ok(body))
    with pytest.raises(LLMOutputError, match="truncated"):
        await llm.structured(req(), Label)


async def test_schema_mismatch_does_not_leak_values() -> None:
    bad = {"label": "user secret text", "confidence": "high", "note": None}
    llm, _ = make_llm(ok(response([message(out_text(json.dumps(bad)))])))
    with pytest.raises(LLMOutputError) as exc:
        await llm.structured(req(), Label)
    assert "confidence" in str(exc.value)
    assert "user secret text" not in str(exc.value)


async def test_empty_output_is_an_error() -> None:
    llm, _ = make_llm(ok(response([])))
    with pytest.raises(LLMOutputError):
        await llm.text(req())


def error(status: int) -> Callable[[httpx2.Request], httpx2.Response]:
    body = {"error": {"message": "boom", "type": "x", "param": None, "code": None}}
    return lambda _: httpx2.Response(status, json=body)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, LLMUnavailableError),
        (500, LLMUnavailableError),
        (503, LLMUnavailableError),
        (400, LLMRejectedError),
        (401, LLMRejectedError),
        (404, LLMRejectedError),
    ],
)
async def test_api_errors_mapped(status: int, expected: type[Exception]) -> None:
    llm, _ = make_llm(error(status))
    with pytest.raises(expected):
        await llm.text(req())


async def test_connection_error_is_retryable() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down", request=request)

    llm, _ = make_llm(fail)
    with pytest.raises(LLMUnavailableError):
        await llm.text(req())


async def test_logs_do_not_contain_prompt_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="recourse.llm.openai_client")
    llm, _ = make_llm(ok(response([message(out_text("secret answer"))])))
    await llm.text(req(documents=[UntrustedDocument(kind="sms", text="private sms body")]))
    assert "purpose=reply.classify" in caplog.text
    assert "private sms body" not in caplog.text
    assert "secret answer" not in caplog.text
