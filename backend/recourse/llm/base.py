"""The LLM interface every part of Recourse uses, plus its request/result types and errors.

Business code (LangGraph nodes, activities) depends only on `LLMClient`. Two implementations:

* `AnthropicLLM` (`anthropic_client.py`): the real API.
* `FakeLLM` (`fake.py`): scripted answers for tests and offline runs. No network, no key.

A request names a *role* (`MAIN` for drafting/judging, `EXTRACT` for extraction and
classification), never a model ID; the client maps roles to models from settings.

Inputs are split by trust:

* `system` and `task` are written by us (trusted).
* `documents` and `attachments` come from users, banks or anyone else (untrusted). They are
  wrapped in delimiters and the model is told never to follow instructions inside them
  (`prompting.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

# =================================================================================================
# Request
# =================================================================================================


class ModelRole(StrEnum):
    MAIN = "MAIN"  # settings.anthropic_main_model: drafting, reply judging
    EXTRACT = "EXTRACT"  # settings.anthropic_extract_model: extraction, classification


Effort = Literal["low", "medium", "high", "xhigh", "max"]
ImageMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
AttachmentMediaType = ImageMediaType | Literal["application/pdf"]

# Our own upload limits (kept under the API's request-size limits).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024


class UntrustedDocument(BaseModel):
    """Text from outside the system: an SMS, a bank reply, a user's description."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(pattern=r"^[a-z_]{1,40}$")  # e.g. "sms", "bank_reply", "user_description"
    text: str = Field(min_length=1, max_length=200_000)


class Attachment(BaseModel):
    """An uploaded image or PDF (untrusted)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    media_type: AttachmentMediaType
    data: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def _size_limit(self) -> Attachment:
        limit = MAX_PDF_BYTES if self.media_type == "application/pdf" else MAX_IMAGE_BYTES
        if len(self.data) > limit:
            raise ValueError(f"{self.media_type} attachment is larger than {limit} bytes")
        return self


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Stable identifier of the call site, e.g. "intake.extract". Used in logs and by FakeLLM.
    purpose: str = Field(pattern=r"^[a-z_]+\.[a-z_]+$")
    role: ModelRole
    system: str = Field(min_length=1)  # trusted; identical across calls so it can be cached
    task: str = Field(min_length=1)  # trusted; this call's instruction, placed after the data
    documents: list[UntrustedDocument] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    max_tokens: int = Field(default=16_000, gt=0, le=64_000)
    effort: Effort | None = None  # None = the model's default
    # Server-side refusal fallback: only for models that support it (the MAIN model).
    allow_fallback: bool = False


# =================================================================================================
# Result
# =================================================================================================


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResult[T]:
    output: T
    served_model: str  # the model that actually answered (differs if a fallback ran)
    usage: LLMUsage
    request_id: str | None = None


# =================================================================================================
# Errors
# =================================================================================================


class LLMError(Exception):
    """Base class. `retryable` tells Temporal's retry policy whether trying again can help."""

    retryable: ClassVar[bool] = False


class LLMUnavailableError(LLMError):
    """Rate limit, overload, timeout or network error. Worth retrying later."""

    retryable = True


class LLMRejectedError(LLMError):
    """The API rejected the request (bad request, auth, missing model). Retrying won't help."""


class LLMRefusalError(LLMError):
    """The model declined to answer (`stop_reason: refusal`)."""

    def __init__(self, message: str, category: str | None = None) -> None:
        super().__init__(message)
        self.category = category


class LLMOutputError(LLMError):
    """The answer was truncated or did not match the requested schema."""


# =================================================================================================
# Interface
# =================================================================================================


class LLMClient(Protocol):
    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        """Ask for JSON matching `schema`; returns a validated instance."""
        ...

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        """Ask for free text (e.g. a complaint draft)."""
        ...
