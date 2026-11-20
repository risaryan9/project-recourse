"""DraftGraph for the complaint to the RBI Ombudsman, with a fake LLM (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from recourse.agents.draft_graph import (
    OMBUDSMAN_PURPOSE,
    DraftKind,
    DraftStatus,
    DraftTemplate,
    run_draft,
)
from recourse.agents.draft_slots import (
    BANK_RESPONSE_TEXT,
    CHANNEL_NAMES,
    BankResponse,
    EscalationContext,
    SlotError,
    build_slots,
    render,
)
from recourse.cases.escalation import escalation_for
from recourse.llm.fake import FakeLLM
from recourse.rules.models import BankType, Channel
from tests.unit.test_draft_graph import TAG, failed_case

LETTER_DATE = date(2026, 12, 19)
EVIDENCE, ENTITLEMENT = failed_case()


def context(response: BankResponse = BankResponse.NO_REPLY) -> EscalationContext:
    assessment = escalation_for(
        ENTITLEMENT,
        bank_type=BankType.COMMERCIAL_BANK,
        complained_on=date(2026, 11, 18),
        as_of=LETTER_DATE,
    )
    assert assessment.can_file_now
    return EscalationContext(
        complained_on=date(2026, 11, 18),
        complaint_ref="CMP48213",
        bank_response=response,
        assessment=assessment,
    )


GOOD = DraftTemplate(
    subject="{{case.tag}} Complaint against my bank: failed UPI payment not reversed",
    body=(
        "To the RBI Ombudsman,\n\n"
        "On {{txn.date}} my account was debited {{txn.amount}} for a UPI payment (reference "
        "{{txn.utr}}), but the beneficiary was not credited. Under {{cite.c1}} the bank had to "
        "reverse it by {{deadline.reversal_deadline}}.\n\n"
        "I complained to the bank on {{bank.complaint_date}} (reference {{bank.complaint_ref}}). "
        "However, {{bank.response}}. I may therefore approach the Ombudsman under {{cite.c5}}.\n\n"
        "Relief sought: reversal of {{money.refund}} and compensation of "
        "{{money.delay_compensation}}, a total of {{money.total}}.\n\n"
        "Yours faithfully,\n{{customer.name}}"
    ),
)


async def test_ombudsman_complaint_is_drafted_and_verified() -> None:
    llm = FakeLLM({OMBUDSMAN_PURPOSE: [GOOD]})
    result = await run_draft(
        llm,
        ENTITLEMENT,
        EVIDENCE,
        case_tag=TAG,
        letter_date=LETTER_DATE,
        kind=DraftKind.OMBUDSMAN_COMPLAINT,
        escalation=context(),
    )
    assert result.status is DraftStatus.DRAFTED
    assert result.kind is DraftKind.OMBUDSMAN_COMPLAINT
    assert result.body is not None
    assert "18 November 2026" in result.body and "CMP48213" in result.body
    assert BANK_RESPONSE_TEXT[BankResponse.NO_REPLY] in result.body
    assert "clause 10(1)(f)" in result.body
    # The prompt is the Ombudsman one, and the reply text never reaches it.
    [call] = llm.calls
    assert "RBI Ombudsman" in call.request.system
    assert "{{bank.response}}" in call.request.task


async def test_missing_bank_response_fails_verify_then_closed() -> None:
    bad = DraftTemplate(subject=GOOD.subject, body=GOOD.body.replace("{{bank.response}}", "no"))
    llm = FakeLLM({OMBUDSMAN_PURPOSE: [bad, bad, bad]})
    result = await run_draft(
        llm,
        ENTITLEMENT,
        EVIDENCE,
        case_tag=TAG,
        letter_date=LETTER_DATE,
        kind=DraftKind.OMBUDSMAN_COMPLAINT,
        escalation=context(),
    )
    assert result.status is DraftStatus.FAILED and result.body is None
    assert any("bank.response" in e for e in result.errors)


async def test_ombudsman_complaint_needs_the_context() -> None:
    with pytest.raises(SlotError):
        await run_draft(
            FakeLLM(),
            ENTITLEMENT,
            EVIDENCE,
            case_tag=TAG,
            letter_date=LETTER_DATE,
            kind=DraftKind.OMBUDSMAN_COMPLAINT,
        )


# --- rendering: the sentence flaws found in the first live run --------------------------------


def test_render_capitalises_and_closes_clause_slots() -> None:
    table = build_slots(
        ENTITLEMENT, EVIDENCE, case_tag=TAG, letter_date=LETTER_DATE, escalation=context()
    )
    text = render(
        "The transaction reference is {{txn.utr}}. {{txn.problem}}\n\n"
        "I complained on {{bank.complaint_date}}. However, {{bank.response}}\n\n"
        "{{bank.response}}, so I write to you.",
        table,
    )
    assert "631845207719. My account was debited but the beneficiary was not credited.\n" in text
    assert "However, the bank did not resolve my complaint" in text  # not capitalised mid-sentence
    assert "within the time allowed.\n" in text  # the missing full stop is added
    assert text.endswith(
        "The bank did not resolve my complaint or send a substantive reply "
        "within the time allowed, so I write to you."
    )


def test_atm_channel_reads_as_a_noun() -> None:
    assert CHANNEL_NAMES[Channel.ATM] == "ATM cash withdrawal"
