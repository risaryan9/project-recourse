"""The assess_escalation activity (bank reply window) against Postgres (Neon test branch).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from recourse.db.models import Case, CaseStatus
from recourse.temporal.case_types import (
    EscalationInput,
    MarkSent,
    PrepareSendInput,
    RecordSentInput,
)
from tests.db.test_case_activities import NOW, events, run
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
