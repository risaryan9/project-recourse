"""LangSmith tracing (ADR-024).

Traces go to one LangSmith *agent* (`LANGSMITH_AGENT_ID`) in one environment
(`LANGSMITH_AGENT_ENVIRONMENT`) at `LANGSMITH_ENDPOINT`, not to a project; legacy project
variables are removed so they cannot redirect traces. LangGraph traces each graph run itself;
the Anthropic and OpenAI clients are wrapped so model calls nest under the graph's run.

What leaves the process is scrubbed by the client's input/output hooks (`scrub`):

* uploaded images and PDFs (bytes, long base64) are replaced with a placeholder, since they
  reach the model unmasked; typed text and replies are already PII-redacted before any model;
* anything shaped like an API key (`sk-…`) is replaced, so a visitor's key (ADR-023) can never
  appear in a trace, even by accident.

Every run of a case carries `thread_id` (the case ID), so one case's runs group as a thread.
Without `LANGSMITH_TRACING=true` and an API key, nothing here does anything.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import uuid
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from recourse.config import Settings

log = logging.getLogger(__name__)

# Legacy destinations that would compete with agent addressing (langchain-agent-tracing).
_LEGACY_ENV = (
    "LANGSMITH_PROJECT",
    "LANGCHAIN_PROJECT",
    "LANGCHAIN_SESSION",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_ENDPOINT",
    "LANGCHAIN_API_KEY",
)
_BASE64 = re.compile(r"[A-Za-z0-9+/=\r\n]{512,}")
# Data URLs (how OpenAI receives images) and long base64 runs inside other text.
_DATA_URL = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=\r\n]+")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{512,}")
_API_KEY = re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}")
ATTACHMENT = "[attachment omitted]"
KEY = "[key omitted]"

_client: Any = None  # the configured langsmith.Client, once tracing is on


def scrub(value: Any) -> Any:
    """A copy of `value` safe to send: no file contents, nothing shaped like an API key."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ATTACHMENT
    if isinstance(value, str):
        if len(value) >= 512 and _BASE64.fullmatch(value):
            return ATTACHMENT
        value = _DATA_URL.sub(ATTACHMENT, value)
        value = _BASE64_RUN.sub(ATTACHMENT, value)
        return _API_KEY.sub(KEY, value)
    if isinstance(value, BaseModel):
        return scrub(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [scrub(v) for v in value]
    return value


def _scrub_dict(data: dict[str, Any]) -> dict[str, Any]:
    out = scrub(data or {})
    return out if isinstance(out, dict) else {"value": out}


def enabled() -> bool:
    return _client is not None


def configure_tracing(settings: Settings) -> bool:
    """Turn tracing on for this process if configured. Returns whether it is on."""
    global _client
    key = settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else ""
    if not settings.langsmith_tracing or not key.strip():
        return False
    if not settings.langsmith_agent_id:
        log.warning("LangSmith tracing is on but LANGSMITH_AGENT_ID is not set; not tracing")
        return False

    import langsmith as ls

    # The SDK and LangChain read these from the environment; set them from our settings.
    for name in _LEGACY_ENV:
        os.environ.pop(name, None)
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_API_KEY"] = key.strip()
    os.environ["LANGSMITH_AGENT_ID"] = settings.langsmith_agent_id
    os.environ["LANGSMITH_AGENT_ENVIRONMENT"] = settings.langsmith_agent_environment
    if settings.langsmith_workspace_id:
        os.environ["LANGSMITH_WORKSPACE_ID"] = settings.langsmith_workspace_id

    _client = ls.Client(
        api_url=settings.langsmith_endpoint,
        api_key=key.strip(),
        workspace_id=settings.langsmith_workspace_id or None,
        hide_inputs=_scrub_dict,
        hide_outputs=_scrub_dict,
    )
    ls.configure(
        client=_client,
        enabled=True,
        address=ls.agent_address(settings.langsmith_agent_id, settings.langsmith_agent_environment),
        tags=["recourse"],
    )
    log.info(
        "LangSmith tracing on: agent=%s environment=%s endpoint=%s",
        settings.langsmith_agent_id,
        settings.langsmith_agent_environment,
        settings.langsmith_endpoint,
    )
    return True


def wrap_client[C](client: C) -> C:
    """The provider client, wrapped so its calls nest in the current trace (if tracing)."""
    if not enabled():
        return client
    from langsmith import wrappers

    module = type(client).__module__
    if module.startswith("anthropic"):
        return wrappers.wrap_anthropic(client)
    if module.startswith("openai"):
        return wrappers.wrap_openai(client)
    return client


@contextlib.contextmanager
def case_trace(step: str, case_id: uuid.UUID) -> Iterator[None]:
    """Runs inside belong to this case's thread, labelled with the step."""
    if not enabled():
        yield
        return
    import langsmith as ls

    meta = {"thread_id": str(case_id), "case_id": str(case_id), "step": step}
    with ls.tracing_context(metadata=meta, tags=["recourse", step]):
        yield


def flush() -> None:
    """Send what is buffered (call before the process exits)."""
    if _client is not None:
        with contextlib.suppress(Exception):
            _client.flush()
