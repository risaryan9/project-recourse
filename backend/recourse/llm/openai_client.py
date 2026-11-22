"""`OpenAILLM`: an `LLMClient` backed by the OpenAI Responses API. Used as the fallback provider
(see `fallback.py`), with the same request, prompt and safety rules as `AnthropicLLM`.

* The prompt is rendered once by `render_prompt` (redaction, untrusted-data wrapping, task last)
  and translated to Responses API input parts here, so both providers see the same prompt.
* Structured output uses `text.format` with a strict JSON schema
  (`openai.lib._pydantic.to_strict_json_schema`); we validate with Pydantic after checking for
  refusals and truncation, like `AnthropicLLM`.
* `store=False`: OpenAI does not keep the request/response for later retrieval.
* `allow_fallback` (Anthropic's server-side refusal fallback) has no OpenAI equivalent and is
  ignored here.
* Logs record purpose, model, token usage and request ID, never prompt or answer content.
"""

from __future__ import annotations

import logging
from typing import Any

import openai
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ValidationError

from recourse.config import Settings
from recourse.llm.base import (
    LLMOutputError,
    LLMRefusalError,
    LLMRejectedError,
    LLMRequest,
    LLMResult,
    LLMUnavailableError,
    LLMUsage,
    ModelRole,
)
from recourse.llm.prompting import RenderedPrompt, render_prompt

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 409, 429})


class OpenAILLM:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None) -> None:
        self._settings = settings
        if client is None:
            from recourse.tracing import wrap_client

            key = settings.openai_api_key
            client = wrap_client(
                openai.AsyncOpenAI(
                    api_key=key.get_secret_value() if key is not None else None,
                    max_retries=settings.openai_max_retries,
                    timeout=settings.openai_timeout_seconds,
                )
            )
        self._client = client

    def model_for(self, role: ModelRole) -> str:
        if role is ModelRole.MAIN:
            return self._settings.openai_main_model
        return self._settings.openai_extract_model

    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        text_config = {
            "format": {
                "type": "json_schema",
                "name": schema.__name__[:64],
                "schema": to_strict_json_schema(schema),
                "strict": True,
            }
        }
        response = await self._call(request, text_config)
        text = _text_of(response)
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as e:
            problems = ", ".join(
                f"{'.'.join(map(str, err['loc']))}:{err['type']}" for err in e.errors()
            )
            raise LLMOutputError(f"{request.purpose}: output failed validation ({problems})") from e
        return self._result(response, parsed)

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        response = await self._call(request, None)
        return self._result(response, _text_of(response))

    # --- internals -------------------------------------------------------------------------------

    async def _call(self, request: LLMRequest, text_config: dict[str, Any] | None) -> Any:
        prompt = render_prompt(request)
        model = self.model_for(request.role)
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": system_text(prompt),
            "input": [{"role": "user", "content": to_input_parts(prompt)}],
            "max_output_tokens": request.max_tokens,
            "store": False,
            "prompt_cache_key": request.purpose,
        }
        if text_config is not None:
            kwargs["text"] = text_config
        if request.effort is not None:
            kwargs["reasoning"] = {"effort": request.effort}

        try:
            response = await self._client.responses.create(**kwargs)
        except openai.APIConnectionError as e:  # includes APITimeoutError
            raise LLMUnavailableError(f"{request.purpose}: connection failed") from e
        except openai.APIStatusError as e:
            message = f"{request.purpose}: API returned {e.status_code}"
            if e.status_code >= 500 or e.status_code in _RETRYABLE_STATUS:
                raise LLMUnavailableError(message) from e
            raise LLMRejectedError(message) from e
        except openai.OpenAIError as e:  # e.g. no credentials configured
            raise LLMRejectedError(f"{request.purpose}: {type(e).__name__}") from e

        usage = response.usage
        log.info(
            "llm call provider=openai purpose=%s model=%s served=%s status=%s in=%s out=%s "
            "cache_read=%s redactions=%s request_id=%s",
            request.purpose,
            model,
            response.model,
            response.status,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            _cached_tokens(usage),
            sorted(prompt.redactions),
            getattr(response, "_request_id", None),
        )

        if _refusal_of(response) is not None:
            raise LLMRefusalError(f"{request.purpose}: model declined")
        if response.status == "incomplete":
            details = response.incomplete_details
            reason = getattr(details, "reason", None)
            if reason == "content_filter":
                raise LLMRefusalError(f"{request.purpose}: blocked by content filter", "filter")
            raise LLMOutputError(f"{request.purpose}: output truncated ({reason})")
        return response

    def _result[T](self, response: Any, output: T) -> LLMResult[T]:
        usage = response.usage
        return LLMResult(
            output=output,
            served_model=response.model,
            usage=LLMUsage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_input_tokens=_cached_tokens(usage) or 0,
            ),
            request_id=getattr(response, "_request_id", None),
        )


# --- prompt translation (Anthropic-style blocks from render_prompt -> Responses input parts) ---


def system_text(prompt: RenderedPrompt) -> str:
    return "\n\n".join(block["text"] for block in prompt.system)


def to_input_parts(prompt: RenderedPrompt) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for i, block in enumerate(prompt.user_content, start=1):
        if block["type"] == "text":
            parts.append({"type": "input_text", "text": block["text"]})
            continue
        source = block["source"]
        data_url = f"data:{source['media_type']};base64,{source['data']}"
        if block["type"] == "image":
            parts.append({"type": "input_image", "image_url": data_url, "detail": "high"})
        elif block["type"] == "document":
            parts.append(
                {"type": "input_file", "filename": f"attachment-{i}.pdf", "file_data": data_url}
            )
        else:  # pragma: no cover - render_prompt only produces the three types above
            raise ValueError(f"unsupported block type {block['type']!r}")
    return parts


def _refusal_of(response: Any) -> str | None:
    for item in response.output or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in item.content or []:
            if getattr(part, "type", None) == "refusal":
                return str(getattr(part, "refusal", "") or "refused")
    return None


def _cached_tokens(usage: Any) -> int | None:
    details = getattr(usage, "input_tokens_details", None)
    return getattr(details, "cached_tokens", None)


def _text_of(response: Any) -> str:
    text: str = response.output_text
    if not text.strip():
        raise LLMOutputError("model returned no text")
    return text
