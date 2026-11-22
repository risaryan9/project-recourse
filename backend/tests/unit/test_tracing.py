"""LangSmith tracing (ADR-024): off unless configured; nothing sensitive leaves the process."""

import base64
import os
import uuid
from collections.abc import Iterator

import langsmith as ls
import pytest
from pydantic import BaseModel

from recourse import tracing
from recourse.config import Settings

IMAGE_B64 = base64.b64encode(os.urandom(2048)).decode()


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_AGENT_ID",
        "LANGSMITH_AGENT_ENVIRONMENT",
        "LANGSMITH_WORKSPACE_ID",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tracing, "_client", None)
    yield
    ls.configure(client=None, enabled=None, address=None, tags=None)
    tracing._client = None


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_scrub_drops_attachments_and_keys() -> None:
    class Attachment(BaseModel):
        media_type: str
        data: bytes

    data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": IMAGE_B64}},
                    {
                        "type": "text",
                        "text": "UPI txn of Rs 2,450 failed. key sk-ant-abcdefghij0123456789",
                    },
                ],
            }
        ],
        "attachments": [Attachment(media_type="image/png", data=b"\x89PNG...")],
        "count": 3,
    }
    out = tracing._scrub_dict(data)
    content = out["messages"][0]["content"]
    assert content[0]["source"]["data"] == tracing.ATTACHMENT
    assert "Rs 2,450 failed" in content[1]["text"]  # ordinary text is kept
    assert "sk-ant-" not in content[1]["text"] and tracing.KEY in content[1]["text"]
    assert out["attachments"][0]["data"] == tracing.ATTACHMENT
    assert out["count"] == 3


def test_scrub_drops_data_urls_and_embedded_base64() -> None:
    openai_style = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{IMAGE_B64}"},
                    },
                    {
                        "type": "file",
                        "file": {"file_data": f"data:application/pdf;base64,{IMAGE_B64}"},
                    },
                ],
            }
        ],
        "note": f"prefix {IMAGE_B64} suffix",
    }
    out = tracing._scrub_dict(openai_style)
    blob = str(out)
    assert IMAGE_B64[:64] not in blob
    assert out["messages"][0]["content"][0]["image_url"]["url"] == tracing.ATTACHMENT
    assert out["note"] == f"prefix {tracing.ATTACHMENT} suffix"


def test_off_without_an_api_key_or_agent() -> None:
    assert tracing.configure_tracing(_settings(langsmith_tracing=True)) is False
    assert (
        tracing.configure_tracing(_settings(langsmith_tracing=True, langsmith_api_key="lsv2_x"))
        is False
    )
    assert not tracing.enabled()
    with tracing.case_trace("intake", uuid.uuid4()):
        pass  # a no-op when off


def test_on_sends_to_the_agent_and_clears_legacy_destinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "old-project")
    on = tracing.configure_tracing(
        _settings(
            langsmith_tracing=True,
            langsmith_api_key="lsv2_test_key",
            langsmith_endpoint="https://api.smith.langchain.com",
            langsmith_agent_id="agentic-track",
            langsmith_agent_environment="production",
        )
    )
    assert on and tracing.enabled()
    assert os.environ["LANGSMITH_AGENT_ID"] == "agentic-track"
    assert os.environ["LANGSMITH_AGENT_ENVIRONMENT"] == "production"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"
    assert "LANGSMITH_PROJECT" not in os.environ
    ctx = ls.get_tracing_context()
    assert ctx["address"] == ls.agent_address("agentic-track", "production")
    with tracing.case_trace("draft:bank_complaint", uuid.UUID(int=7)):
        meta = ls.get_tracing_context()["metadata"]
        assert meta["thread_id"] == str(uuid.UUID(int=7)) and meta["step"] == "draft:bank_complaint"
