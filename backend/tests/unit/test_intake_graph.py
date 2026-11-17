"""IntakeGraph with a scripted FakeLLM: no network, no API key. All data is synthetic."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from recourse.agents.intake_graph import (
    CLASSIFY_PURPOSE,
    EXTRACT_PURPOSE,
    IntakeInput,
    IntakeStatus,
    apply_corrections,
    run_intake,
)
from recourse.agents.intake_schema import Classification, ExtractionOutput, to_evidence
from recourse.evidence.models import IST, CaseType, Evidence, Origin, SourceKind
from recourse.llm.base import Attachment, LLMUnavailableError
from recourse.llm.fake import FakeLLM
from recourse.llm.prompting import UNTRUSTED_TAG
from recourse.rules.models import Channel, FailedScenario
from recourse.security.pii import RedactionKind

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)

SMS = (
    "Rs 2,450.00 debited from A/c XX4821 on 14-11-26 18:05 via UPI (UPI Ref No 631845207719) "
    "to merchant@okaxis. Txn FAILED. -Example Bank"
)


def fact(value: Any, confidence: float = 0.95, source: str | None = "from sms") -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "source_text": source}


NONE = {"value": None, "confidence": 0.0, "source_text": None}


def extraction(**overrides: Any) -> dict[str, Any]:
    """A complete, valid extraction of SMS (a failed UPI transfer)."""
    data: dict[str, Any] = {name: NONE for name in Evidence.fact_names()}
    data.update(
        case_type=fact("failed_transaction", 0.9, None),
        scenario=fact("BENEFICIARY_NOT_CREDITED", 0.9, None),
        channel=fact("UPI", source="via UPI"),
        txn_status=fact("FAILED", source="Txn FAILED"),
        account_debited=fact(True, source="debited"),
        amount_inr=fact("Rs 2,450.00", source="Rs 2,450.00 debited"),
        txn_timestamp=fact("2026-11-14T18:05:00", source="on 14-11-26 18:05"),
        is_domestic=fact(True, 0.9, None),
        utr=fact("631845207719", source="UPI Ref No 631845207719"),
        payer_bank=fact("Example Bank", source="-Example Bank"),
        payer_account_last4=fact("4821", source="A/c XX4821"),
        payee_vpa=fact("merchant@okaxis", source="to merchant@okaxis"),
        notes=[],
    )
    data.update(overrides)
    return data


def classified(kind: str = "SMS") -> dict[str, str]:
    return {"kind": kind, "reason": "A bank SMS about a UPI debit."}


async def test_happy_path_is_ready() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction()]})

    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)

    assert result.status is IntakeStatus.READY
    assert result.needs_user_confirmation == []
    ev = result.evidence
    assert ev is not None
    assert ev.source_kind is SourceKind.SMS
    assert ev.extracted_by == "fake-extract"
    assert ev.case_type.value is CaseType.FAILED_TRANSACTION
    assert ev.scenario.value is FailedScenario.BENEFICIARY_NOT_CREDITED
    assert ev.channel.value is Channel.UPI
    assert ev.amount_inr.value == Decimal("2450.00")
    assert ev.txn_timestamp.value == datetime(2026, 11, 14, 18, 5, tzinfo=IST)
    assert ev.utr.value == "631845207719"
    assert ev.payer_account_last4.value == "4821"
    assert result.models_used == ["fake-extract", "fake-extract"]
    assert llm.unused == {}


async def test_prompts_wrap_input_and_give_todays_date() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction()]})
    png = Attachment(media_type="image/png", data=b"\x89PNG fake")

    await run_intake(llm, IntakeInput(text=SMS, attachments=[png]), now=NOW)

    [classify_call] = llm.calls_for(CLASSIFY_PURPOSE)
    [extract_call] = llm.calls_for(EXTRACT_PURPOSE)
    assert classify_call.schema is Classification
    assert extract_call.schema is ExtractionOutput
    for call in (classify_call, extract_call):
        assert call.request.attachments == [png]
        assert f'<{UNTRUSTED_TAG} index="1" kind="customer_submission">' in call.prompt.user_text
    assert "Today's date is 2026-11-17 (IST)" in extract_call.request.task
    assert "The evidence above is an SMS." in extract_call.request.task


async def test_today_uses_ist_date() -> None:
    # 20:00 UTC on the 16th is 01:30 on the 17th in India.
    now = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction()]})
    await run_intake(llm, IntakeInput(text=SMS), now=now)
    assert "2026-11-17" in llm.calls_for(EXTRACT_PURPOSE)[0].request.task


async def test_unrelated_input_stops_before_extraction() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [{"kind": "UNRELATED", "reason": "A photo of a cat."}]})

    result = await run_intake(llm, IntakeInput(text="look at my cat"), now=NOW)

    assert result.status is IntakeStatus.NOT_PAYMENT_EVIDENCE
    assert result.evidence is None
    assert result.reason == "A photo of a cat."
    assert llm.calls_for(EXTRACT_PURPOSE) == []


async def test_missing_utr_needs_confirmation() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction(utr=NONE)]})
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert result.status is IntakeStatus.NEEDS_CONFIRMATION
    assert result.needs_user_confirmation == ["utr"]


async def test_low_confidence_amount_needs_confirmation() -> None:
    blurry = fact("Rs 2,450.00", confidence=0.4, source="Rs 2,4?0.00")
    llm = FakeLLM(
        {
            CLASSIFY_PURPOSE: [classified("SCREENSHOT")],
            EXTRACT_PURPOSE: [extraction(amount_inr=blurry)],
        }
    )
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert result.status is IntakeStatus.NEEDS_CONFIRMATION
    assert "amount_inr" in result.needs_user_confirmation


async def test_unparseable_value_becomes_missing_not_a_guess() -> None:
    garbled = extraction(
        amount_inr=fact("two thousand-ish"),
        txn_timestamp=fact("last Tuesday evening"),
        card_last4=fact("12"),
    )
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [garbled]})

    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)

    ev = result.evidence
    assert ev is not None
    assert not ev.amount_inr.present
    assert not ev.txn_timestamp.present
    assert not ev.card_last4.present
    assert {"amount_inr", "txn_timestamp"} <= set(result.needs_user_confirmation)
    assert "Could not read amount_inr; please enter it." in ev.notes
    assert "Could not read card_last4; please enter it." in ev.notes


async def test_date_without_time_is_accepted() -> None:
    llm = FakeLLM(
        {
            CLASSIFY_PURPOSE: [classified()],
            EXTRACT_PURPOSE: [extraction(txn_timestamp=fact("2026-11-14"))],
        }
    )
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert result.evidence is not None
    assert result.evidence.txn_date == datetime(2026, 11, 14).date()


async def test_future_date_is_flagged() -> None:
    future = extraction(txn_timestamp=fact("2026-12-25T10:00:00"))
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [future]})
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert "txn_timestamp" in result.needs_user_confirmation


async def test_pii_in_model_output_is_redacted() -> None:
    leaky = extraction(
        card_last4=fact("4111 1111 1111 1111", source="Card 4111 1111 1111 1111"),
        other_reference=fact("ORD123", source="OTP 482913 for order ORD123"),
        notes=["Customer shared OTP 482913 in the chat"],
    )
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [leaky]})

    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)

    ev = result.evidence
    assert ev is not None
    dumped = ev.model_dump_json()
    assert "4111 1111 1111 1111" not in dumped
    assert "482913" not in dumped
    assert ev.card_last4.value == "1111"
    assert RedactionKind.SECRET_CODE in ev.redactions


async def test_secrets_in_the_input_are_recorded_for_a_warning() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction()]})
    result = await run_intake(llm, IntakeInput(text=SMS + " OTP 482913"), now=NOW)
    assert result.evidence is not None
    assert RedactionKind.SECRET_CODE in result.evidence.redactions
    assert "482913" not in llm.calls_for(EXTRACT_PURPOSE)[0].prompt.user_text


async def test_amount_written_in_words_of_currency() -> None:
    llm = FakeLLM(
        {
            CLASSIFY_PURPOSE: [classified()],
            EXTRACT_PURPOSE: [extraction(amount_inr=fact("5000 rupees"))],
        }
    )
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert result.evidence is not None
    assert result.evidence.amount_inr.value == Decimal("5000")


async def test_confidence_is_clamped() -> None:
    odd = extraction(utr=fact("631845207719", confidence=1.7))
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [odd]})
    result = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert result.evidence is not None
    assert result.evidence.utr.confidence == 1.0


async def test_llm_errors_propagate() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [LLMUnavailableError("overloaded")]})
    with pytest.raises(LLMUnavailableError):
        await run_intake(llm, IntakeInput(text=SMS), now=NOW)


async def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        await run_intake(FakeLLM(), IntakeInput(text=SMS), now=datetime(2026, 11, 17))


def test_empty_input_rejected() -> None:
    with pytest.raises(ValidationError):
        IntakeInput(text="   ")


# --- apply_corrections ------------------------------------------------------------------------


async def test_user_corrections_make_the_case_ready() -> None:
    llm = FakeLLM(
        {
            CLASSIFY_PURPOSE: [classified()],
            EXTRACT_PURPOSE: [extraction(utr=NONE, amount_inr=fact("Rs 2,450.00", 0.3))],
        }
    )
    first = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert first.status is IntakeStatus.NEEDS_CONFIRMATION
    assert first.evidence is not None

    second = apply_corrections(
        first.evidence, {"utr": "631845207719", "amount_inr": "2450"}, now=NOW
    )

    assert second.status is IntakeStatus.READY
    assert second.evidence is not None
    assert second.evidence.utr.origin is Origin.USER
    assert second.evidence.amount_inr.value == Decimal("2450")
    assert second.models_used == []


async def test_bad_user_value_raises() -> None:
    llm = FakeLLM({CLASSIFY_PURPOSE: [classified()], EXTRACT_PURPOSE: [extraction()]})
    first = await run_intake(llm, IntakeInput(text=SMS), now=NOW)
    assert first.evidence is not None
    with pytest.raises(ValidationError):
        apply_corrections(first.evidence, {"amount_inr": "lots"}, now=NOW)


# --- schema ---------------------------------------------------------------------------------


def test_extraction_form_covers_every_evidence_fact() -> None:
    form = set(ExtractionOutput.model_fields) - {"notes"}
    assert form == set(Evidence.fact_names())


def test_to_evidence_keeps_drop_notes_when_notes_are_full() -> None:
    out = ExtractionOutput.model_validate(
        extraction(amount_inr=fact("??"), notes=[f"note {i}" for i in range(5)])
    )
    ev = to_evidence(out, source_kind=SourceKind.SMS, extracted_by="m")
    assert ev.notes[-1] == "Could not read amount_inr; please enter it."
    assert len(ev.notes) <= 10
