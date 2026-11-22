"""`AnthropicLLM`: the real `LLMClient`, backed by the Anthropic Messages API.

* Model IDs come from settings by role (`ModelRole.MAIN` / `EXTRACT`).
* Structured output uses `output_config.format` with the schema from
  `anthropic.transform_schema` (which moves constraints the API does not support into the
  description). We validate the JSON ourselves with Pydantic *after* checking `stop_reason`,
  so a refusal or a truncated answer is reported as such instead of as a parse error.
* The system prompt carries a prompt-cache breakpoint (see `prompting.py`).
* `allow_fallback=True` turns on the server-side refusal fallback
  (`fallbacks="default"`), for models that support it.
* API errors become `LLMError` subclasses with a `retryable` flag for Temporal.
* Logs record purpose, model, token usage and request ID, never prompt or answer content.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
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
from recourse.llm.prompting import render_prompt

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Status codes worth retrying (same set the SDK retries itself).
_RETRYABLE_STATUS = frozenset({408, 409, 429})


class AnthropicLLM:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        if client is None:
            from recourse.tracing import wrap_client

            key = settings.anthropic_api_key
            client = wrap_client(
                anthropic.AsyncAnthropic(
                    api_key=key.get_secret_value() if key is not None else None,
                    max_retries=settings.anthropic_max_retries,
                    timeout=settings.anthropic_timeout_seconds,
                )
            )
        self._client = client

    def model_for(self, role: ModelRole) -> str:
        if role is ModelRole.MAIN:
            return self._settings.anthropic_main_model
        return self._settings.anthropic_extract_model

    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        }
        response = await self._call(request, output_config)
        text = _text_of(response)
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as e:
            # Field names and error types only; never the values (they may be user data).
            problems = ", ".join(
                f"{'.'.join(map(str, err['loc']))}:{err['type']}" for err in e.errors()
            )
            raise LLMOutputError(f"{request.purpose}: output failed validation ({problems})") from e
        return self._result(response, parsed)

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        response = await self._call(request, None)
        return self._result(response, _text_of(response))

    # --- internals -------------------------------------------------------------------------------

    async def _call(self, request: LLMRequest, output_config: dict[str, Any] | None) -> Any:
        prompt = render_prompt(request)
        model = self.model_for(request.role)
        config: dict[str, Any] = dict(output_config or {})
        if request.effort is not None:
            config["effort"] = request.effort

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": prompt.system,
            "messages": [{"role": "user", "content": prompt.user_content}],
        }
        if config:
            kwargs["output_config"] = config
        if request.allow_fallback:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            response = await self._client.beta.messages.create(**kwargs)
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            raise LLMUnavailableError(f"{request.purpose}: connection failed") from e
        except anthropic.APIStatusError as e:
            message = f"{request.purpose}: API returned {e.status_code}"
            if e.status_code >= 500 or e.status_code in _RETRYABLE_STATUS:
                raise LLMUnavailableError(message) from e
            raise LLMRejectedError(message) from e
        except anthropic.AnthropicError as e:  # e.g. no credentials configured
            raise LLMRejectedError(f"{request.purpose}: {type(e).__name__}") from e

        usage = response.usage
        log.info(
            "llm call purpose=%s model=%s served=%s stop=%s in=%s out=%s cache_read=%s "
            "cache_write=%s redactions=%s request_id=%s",
            request.purpose,
            model,
            response.model,
            response.stop_reason,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens,
            usage.cache_creation_input_tokens,
            sorted(prompt.redactions),
            getattr(response, "_request_id", None),
        )

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = getattr(details, "category", None) if details is not None else None
            raise LLMRefusalError(f"{request.purpose}: model declined", category=category)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(f"{request.purpose}: output truncated at max_tokens")
        return response

    def _result[T](self, response: Any, output: T) -> LLMResult[T]:
        usage = response.usage
        return LLMResult(
            output=output,
            served_model=response.model,
            usage=LLMUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
            request_id=getattr(response, "_request_id", None),
        )


def _text_of(response: Any) -> str:
    """Join the answer's text blocks (thinking blocks are skipped)."""
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise LLMOutputError("model returned no text")
    return text
