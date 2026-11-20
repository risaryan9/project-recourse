"""The assess_escalation activity (bank reply window) against Postgres (Neon test branch).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from recourse.agents.draft_graph import OMBUDSMAN_PURPOSE, DraftKind, DraftStatus
from recourse.agents.reply_judge_graph import CLASSIFY_PURPOSE, NextAction, ReplyLabel
from recourse.db.models import BankReply, Case, CaseStatus, DraftRecord, ReplySource, ReplyStatus
from recourse.db.models import DraftKind as DbDraftKind
from recourse.llm.fake import FakeLLM
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    CloseOutcome,
    CloseRequest,
    DraftDecisionInput,
    DraftInput,
    EscalationInput,
    JudgeReplyInput,
    MarkFiled,
    MarkResolved,
    MarkSent,
    PrepareSendInput,
    RecordClosedInput,
    RecordFiledInput,
    RecordResolvedInput,
    RecordSentInput,
)
from tests.db.test_case_activities import NOW, events, run
from tests.db.test_draft_activities import approve_with
from tests.db.test_send_activities import SANDBOX, approved_draft, sender

pytestmark = pytest.mark.db

TODAY = date(2026, 11, 17)


async def sent_case(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """A failed-UPI case whose complaint was sent on 17 Nov 2026 (IST)."""
    case_id, draft_id = await approved_draft(sessions)
    acts = sender(sessions, SANDBOX)
    summary = await run(acts.prepare_send, PrepareSendInput(case_id=case_id, draft_id=draft_id))
    await run(
        acts.record_sent,
        RecordSentInput(
            case_id=case_id,
            email_id=summary.email_id,
            mark=MarkSent(sent_at=NOW + timedelta(minutes=5)),
            now=NOW + timedelta(minutes=6),
        ),
    )
    return case_id


async def test_bank_wait_starts_from_the_send_date(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    acts = sender(sessions, SANDBOX)
    inp = EscalationInput(
        case_id=case_id,
        as_of=TODAY,
        event="bank.wait_started",
        new_status=CaseStatus.AWAITING_BANK,
    )
    summary = await run(acts.assess_escalation, inp)
    # Failed transaction: no bank timeline, so the scheme's 30 days apply.
    # 17 Nov + 30 = 17 Dec (waiting period ends); eligible 18 Dec.
    # Deadline 17 Dec + 90: 14 (to 31 Dec) + 31 + 28 = 73, + 17 -> 17 Mar 2027.
    assert summary.eligible_from == date(2026, 12, 18)
    assert summary.filing_deadline == date(2027, 3, 17)
    assert summary.wait_days == 31
    assert not summary.can_file_now
    # "Example Bank" is not in the contacts file and the user has not said its type.
    assert summary.entity_coverage == "UNKNOWN" and summary.needs_bank_type

    assert await run(acts.assess_escalation, inp) == summary  # idempotent
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        assert case.status is CaseStatus.AWAITING_BANK
        assert case.bank_complained_on == date(2026, 11, 17)
        assert case.escalation is not None
        assert case.escalation["filing_deadline"] == "2027-03-17"
        assert (await events(s, case_id)).count("bank.wait_started") == 1


async def test_bank_type_comes_from_the_contacts_file(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    async with sessions.begin() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        case.bank_id = "pnb"  # a commercial bank in the contacts file
    acts = sender(sessions, SANDBOX)
    summary = await run(
        acts.assess_escalation,
        EscalationInput(case_id=case_id, as_of=date(2026, 12, 18), event="bank.wait_expired"),
    )
    assert summary.entity_coverage == "COVERED" and not summary.needs_bank_type
    assert summary.can_file_now and summary.wait_days == 0
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.SENT_TO_BANK  # status unchanged


async def test_needs_a_sent_complaint(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id, _ = await approved_draft(sessions)
    acts = sender(sessions, SANDBOX)
    with pytest.raises(ApplicationError, match="not been sent"):
        await run(
            acts.assess_escalation,
            EscalationInput(case_id=case_id, as_of=TODAY, event="bank.wait_started"),
        )


# --- bank replies -------------------------------------------------------------------------------


async def store_reply(
    sessions: async_sessionmaker[AsyncSession],
    case_id: uuid.UUID,
    text: str,
    received_at: datetime,
    sender: str | None = None,
) -> uuid.UUID:
    async with sessions.begin() as s:
        row = BankReply(
            case_id=case_id,
            source=ReplySource.PASTE,
            received_at=received_at,
            sender=sender,
            body=text,
            redactions=[],
            status=ReplyStatus.PENDING,
        )
        s.add(row)
        await s.flush()
        return row.id


def judge(
    sessions: async_sessionmaker[AsyncSession], *answers: dict[str, Any]
) -> tuple[CaseActivities, FakeLLM]:
    llm = FakeLLM({CLASSIFY_PURPOSE: list(answers)})
    return CaseActivities(sessions, llm, settings=SANDBOX), llm


async def test_acknowledgement_keeps_waiting_and_records_the_reference(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    reply_id = await store_reply(
        sessions, case_id, "Complaint registered, ref CMP48213.", NOW + timedelta(days=1)
    )
    acts, llm = judge(sessions, {"label": "ACKNOWLEDGEMENT", "complaint_number": "CMP48213"})
    inp = JudgeReplyInput(case_id=case_id, reply_id=reply_id)
    summary = await run(acts.judge_reply, inp)
    assert summary.label is ReplyLabel.ACKNOWLEDGEMENT
    assert summary.next_action is NextAction.KEEP_WAITING
    assert summary.complaint_number == "CMP48213"
    assert await run(acts.judge_reply, inp) == summary  # idempotent: no second LLM call
    assert len(llm.calls) == 1

    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        assert case.status is CaseStatus.AWAITING_BANK
        assert case.bank_complaint_ref == "CMP48213"
        assert (await events(s, case_id)).count("reply.judged") == 1


async def test_payment_short_of_what_is_owed_is_escalated(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    reply_id = await store_reply(
        sessions, case_id, "Rs 2450 has been reversed.", NOW + timedelta(days=3)
    )
    acts, _ = judge(sessions, {"label": "RESOLVED", "amount_reversed_inr": "2450"})
    summary = await run(acts.judge_reply, JudgeReplyInput(case_id=case_id, reply_id=reply_id))
    assert summary.next_action is NextAction.ESCALATE  # the delay compensation is missing

    async with sessions() as s:
        reply = await s.get(BankReply, reply_id)
        assert reply is not None and reply.unsatisfactory
        assert reply.verdict is not None and Decimal(reply.verdict["shortfall_inr"]) > 0
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.JUDGED

    # The unsatisfactory reply on 20 Nov makes the case eligible for the Ombudsman at once.
    esc = await run(
        acts.assess_escalation,
        EscalationInput(case_id=case_id, as_of=date(2026, 11, 20), event="bank.replied"),
    )
    assert esc.eligible_from == date(2026, 11, 20)
    assert esc.filing_deadline == date(2027, 3, 17)  # still 90 days after the waiting period


async def test_phishing_reply_is_flagged_without_the_llm(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    reply_id = await store_reply(
        sessions,
        case_id,
        "Refund ready. Share the OTP you receive to process it. Ref CMP99999.",
        NOW + timedelta(days=1),
    )
    acts, llm = judge(sessions)  # nothing scripted: an LLM call would fail
    summary = await run(acts.judge_reply, JudgeReplyInput(case_id=case_id, reply_id=reply_id))
    assert summary.suspicious and summary.next_action is NextAction.WARN
    assert summary.complaint_number is None
    assert llm.calls == []
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        assert case.status is CaseStatus.AWAITING_BANK and case.bank_complaint_ref is None
        assert "reply.suspicious" in await events(s, case_id)

    # A suspicious reply is not a bank communication: the dates do not move.
    esc = await run(
        acts.assess_escalation,
        EscalationInput(case_id=case_id, as_of=TODAY, event="bank.replied"),
    )
    assert esc.eligible_from == date(2026, 12, 18)


async def test_record_resolved(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id = await sent_case(sessions)
    acts = sender(sessions, SANDBOX)
    mark = RecordResolvedInput(
        case_id=case_id, mark=MarkResolved(amount_received_inr=Decimal("2750")), now=NOW
    )
    with pytest.raises(ApplicationError, match="cannot be marked resolved"):
        await run(acts.record_resolved, mark)  # still SENT_TO_BANK: the timer has not started
    await run(
        acts.assess_escalation,
        EscalationInput(
            case_id=case_id,
            as_of=TODAY,
            event="bank.wait_started",
            new_status=CaseStatus.AWAITING_BANK,
        ),
    )
    await run(acts.record_resolved, mark)
    await run(acts.record_resolved, mark)  # idempotent
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.RESOLVED
        assert (await events(s, case_id)).count("case.resolved") == 1


# --- the complaint to the RBI Ombudsman ---------------------------------------------------------

OMBUDSMAN_TEMPLATE = {
    "subject": "{{case.tag}} Complaint against my bank: failed UPI payment not reversed",
    "body": (
        "To the RBI Ombudsman,\n\n"
        "On {{txn.date}} my account was debited {{txn.amount}} (reference {{txn.utr}}). Under "
        "{{cite.c1}} the bank had to reverse it by {{deadline.reversal_deadline}}.\n\n"
        "I complained to the bank on {{bank.complaint_date}}. However, {{bank.response}}. I may "
        "therefore approach the Ombudsman under {{cite.c5}}.\n\n"
        "Relief sought: {{money.refund}} and compensation of {{money.delay_compensation}}, a "
        "total of {{money.total}}.\n\n"
        "Yours faithfully,\n{{customer.name}}"
    ),
}
ELIGIBLE_DAY = date(2026, 12, 18)


async def test_ombudsman_complaint_from_draft_to_closed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await sent_case(sessions)
    async with sessions.begin() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        case.bank_id = "pnb"  # a commercial bank: covered by the scheme
    llm = FakeLLM({OMBUDSMAN_PURPOSE: [OMBUDSMAN_TEMPLATE]})
    acts = CaseActivities(sessions, llm, settings=SANDBOX)
    await run(
        acts.assess_escalation,
        EscalationInput(
            case_id=case_id,
            as_of=TODAY,
            event="bank.wait_started",
            new_status=CaseStatus.AWAITING_BANK,
        ),
    )
    letter = DraftInput(
        case_id=case_id, seq=2, letter_date=ELIGIBLE_DAY, kind=DraftKind.OMBUDSMAN_COMPLAINT
    )
    with pytest.raises(ApplicationError, match="cannot be filed now"):
        await run(acts.draft, letter)  # the waiting period has not ended yet
    assert llm.calls == []

    await run(
        acts.assess_escalation,
        EscalationInput(case_id=case_id, as_of=ELIGIBLE_DAY, event="escalation.checked"),
    )
    summary = await run(acts.draft, letter)
    assert summary.status is DraftStatus.DRAFTED
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.ESCALATION_DRAFT_READY
        row = await s.get(DraftRecord, summary.draft_id)
        assert row is not None and row.kind is DbDraftKind.OMBUDSMAN_COMPLAINT
        assert row.context is not None and row.context["bank_response"] == "NO_REPLY"
        assert row.body is not None and "17 November 2026" in row.body
        # Re-assessed on the letter date: 33 days of delay since the 15 Nov deadline.
        assert "₹3,300.00" in row.body and "case.reassessed" in await events(s, case_id)

    # Approve (the slot table is rebuilt from the stored context), file, close.
    await approve_with(sessions, summary.draft_id)
    approved_at = NOW + timedelta(days=31)
    await run(
        acts.approve_draft,
        DraftDecisionInput(case_id=case_id, draft_id=summary.draft_id, now=approved_at),
    )
    filed = RecordFiledInput(
        case_id=case_id,
        draft_id=summary.draft_id,
        mark=MarkFiled(filed_at=approved_at + timedelta(hours=1), reference="N-0042"),
        now=approved_at + timedelta(hours=2),
    )
    await run(acts.record_filed, filed)
    await run(acts.record_filed, filed)  # idempotent
    closed = RecordClosedInput(
        case_id=case_id, request=CloseRequest(outcome=CloseOutcome.AWARD_RECEIVED)
    )
    await run(acts.record_closed, closed)
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.CLOSED
        assert case.ombudsman_ref == "N-0042"
        names = await events(s, case_id)
        assert names.count("ombudsman.filed") == 1 and names[-1] == "case.closed"
