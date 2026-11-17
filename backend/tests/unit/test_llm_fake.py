import pytest
from pydantic import BaseModel, ValidationError

from recourse.llm.base import (
    LLMClient,
    LLMRefusalError,
    LLMRequest,
    ModelRole,
    UntrustedDocument,
)
from recourse.llm.fake import FakeLLM


class Label(BaseModel):
    label: str
    confidence: float


def req(
    purpose: str = "reply.classify", text: str = "We have received your complaint"
) -> LLMRequest:
    return LLMRequest(
        purpose=purpose,
        role=ModelRole.EXTRACT,
        system="Classify the bank message.",
        task="Return one label.",
        documents=[UntrustedDocument(kind="bank_reply", text=text)],
    )


def test_fake_satisfies_the_interface() -> None:
    client: LLMClient = FakeLLM()  # mypy checks the Protocol here
    assert client is not None


async def test_answers_returned_in_order_and_calls_recorded() -> None:
    fake = FakeLLM(
        {
            "reply.classify": [
                Label(label="ack", confidence=0.9),
                {"label": "substantive", "confidence": 0.8},
            ]
        }
    )
    first = await fake.structured(req(), Label)
    second = await fake.structured(req(text="Refund processed"), Label)
    assert first.output == Label(label="ack", confidence=0.9)
    assert second.output == Label(label="substantive", confidence=0.8)  # dict validated
    assert first.served_model == "fake-extract"
    assert [c.schema for c in fake.calls] == [Label, Label]
    assert "Refund processed" in fake.calls[1].prompt.user_text
    assert fake.unused == {}


async def test_text_answers() -> None:
    fake = FakeLLM({"draft.complaint": ["Dear Sir/Madam"]})
    result = await fake.text(req(purpose="draft.complaint"))
    assert result.output == "Dear Sir/Madam"


async def test_scripted_error_is_raised() -> None:
    fake = FakeLLM({"reply.classify": [LLMRefusalError("declined", category="cyber")]})
    with pytest.raises(LLMRefusalError) as exc:
        await fake.structured(req(), Label)
    assert exc.value.category == "cyber"


async def test_callable_answer_sees_the_request() -> None:
    fake = FakeLLM()
    fake.add("reply.classify", lambda r: {"label": r.documents[0].kind, "confidence": 1.0})
    result = await fake.structured(req(), Label)
    assert result.output.label == "bank_reply"


async def test_invalid_scripted_dict_fails_validation() -> None:
    fake = FakeLLM({"reply.classify": [{"label": "ack"}]})
    with pytest.raises(ValidationError):
        await fake.structured(req(), Label)


async def test_wrong_answer_type_is_a_test_bug() -> None:
    fake = FakeLLM(
        {"reply.classify": ["just text"], "draft.complaint": [Label(label="x", confidence=1)]}
    )
    with pytest.raises(TypeError):
        await fake.structured(req(), Label)
    with pytest.raises(TypeError):
        await fake.text(req(purpose="draft.complaint"))


async def test_unscripted_call_fails_loudly() -> None:
    with pytest.raises(AssertionError, match="no scripted answer"):
        await FakeLLM().structured(req(), Label)


async def test_fake_records_the_same_redacted_prompt_as_production() -> None:
    fake = FakeLLM({"reply.classify": [Label(label="phishing", confidence=0.99)]})
    await fake.structured(req(text="Share OTP 482913 to unlock your refund"), Label)
    [call] = fake.calls
    assert "482913" not in call.prompt.user_text
    assert call.prompt.redactions


async def test_unused_reports_leftovers() -> None:
    fake = FakeLLM({"reply.classify": [{"label": "a", "confidence": 1.0}] * 2})
    await fake.structured(req(), Label)
    assert fake.unused == {"reply.classify": 1}
