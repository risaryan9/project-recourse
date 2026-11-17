"""`FallbackLLM`: try the primary provider, switch to the next one on a provider failure.

Falls back only on *provider* problems, where another provider can genuinely help:

* `LLMUnavailableError`: rate limit, overload, timeout, network.
* `LLMRejectedError`: bad/expired key, missing model, request the provider won't accept.

It does **not** fall back on:

* `LLMRefusalError`: a model declining is a safety decision; we don't shop the request around
  until some model agrees.
* `LLMOutputError`: a truncated or malformed answer is a prompt/limit problem; the caller (or
  Temporal's retry) decides what to do.

If every provider fails: when any failure was retryable (e.g. one provider overloaded, the other
misconfigured), an `LLMUnavailableError` is raised so Temporal tries again later; otherwise the
last provider's error is raised. Every failure is logged (metadata only).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel

from recourse.llm.base import (
    LLMClient,
    LLMError,
    LLMRejectedError,
    LLMRequest,
    LLMResult,
    LLMUnavailableError,
)

log = logging.getLogger(__name__)

_FALLBACK_ON = (LLMUnavailableError, LLMRejectedError)


class FallbackLLM:
    def __init__(self, providers: Sequence[tuple[str, LLMClient]]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self._providers = list(providers)

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self._providers]

    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        errors: list[LLMError] = []
        for name, client in self._providers:
            try:
                return await client.structured(request, schema)
            except _FALLBACK_ON as e:
                errors.append(self._note_failure(request, name, e))
        raise _final_error(request, errors)

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        errors: list[LLMError] = []
        for name, client in self._providers:
            try:
                return await client.text(request)
            except _FALLBACK_ON as e:
                errors.append(self._note_failure(request, name, e))
        raise _final_error(request, errors)

    def _note_failure(self, request: LLMRequest, name: str, error: LLMError) -> LLMError:
        log.warning(
            "llm provider failed purpose=%s provider=%s error=%s retryable=%s",
            request.purpose,
            name,
            type(error).__name__,
            error.retryable,
        )
        return error


def _final_error(request: LLMRequest, errors: list[LLMError]) -> LLMError:
    last = errors[-1]
    if len(errors) > 1 and not last.retryable and any(e.retryable for e in errors):
        retryable = LLMUnavailableError(f"{request.purpose}: all LLM providers failed")
        retryable.__cause__ = last
        return retryable
    return last
