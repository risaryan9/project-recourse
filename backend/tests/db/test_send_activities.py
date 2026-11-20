"""Send activities (send policy + ManualChannel) against Postgres (Neon test branch).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from recourse.config import Settings
from recourse.db.models import Case, CaseStatus, OutboundEmail, OutboundStatus
from recourse.email.channel import ChannelKind
from recourse.llm.fake import FakeLLM
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    DraftDecisionInput,
    DraftInput,
    MarkSent,
    PrepareSendInput,
    RecordSentInput,
)
from tests.db.test_case_activities import NOW, events, run
from tests.db.test_draft_activities import LETTER_DATE, approve_with, assessed_case, drafter
from tests.unit.test_draft_graph import GOOD_FAILED

pytestmark = pytest.mark.db

SANDBOX = Settings(email_sandbox=True, sandbox_recipient="sandbox@example.com")
REAL = Settings(email_sandbox=False, sandbox_recipient=None)


async def approved_draft(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID]:
    case_id = await assessed_case(sessions)
    acts, _ = drafter(sessions, GOOD_FAILED.model_dump())
    summary = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    await approve_with(sessions, summary.draft_id)
    await run(
        acts.approve_draft,
        DraftDecisionInput(case_id=case_id, draft_id=summary.draft_id, now=NOW),
    )
    return case_id, summary.draft_id


def sender(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> CaseActivities:
    return CaseActivities(sessions, FakeLLM({}), settings=settings)


async def test_sandbox_send_is_prepared_for_the_user(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, draft_id = await approved_draft(sessions)
    acts = sender(sessions, SANDBOX)
    inp = PrepareSendInput(case_id=case_id, draft_id=draft_id)

    summary = await run(acts.prepare_send, inp)
    assert summary.status is OutboundStatus.PREPARED and summary.sandbox
    assert summary.channel is ChannelKind.MANUAL
    assert await run(acts.prepare_send, inp) == summary  # idempotent

    async with sessions() as s:
        row = (await s.scalars(select(OutboundEmail))).one()
        assert row.recipient == "sandbox@example.com"
        assert row.body.endswith("Asha Verma")  # the approved final text
        assert row.bank_id is None  # "Example Bank" is not in the contacts file
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.AWAITING_APPROVAL
        assert (await events(s, case_id))[-1] == "email.prepared"

    # The user sent it: SENT_TO_BANK.
    mark = MarkSent(sent_at=NOW + timedelta(minutes=5))
    record = RecordSentInput(
        case_id=case_id, email_id=summary.email_id, mark=mark, now=NOW + timedelta(minutes=6)
    )
    await run(acts.record_sent, record)
    await run(acts.record_sent, record)  # idempotent
    async with sessions() as s:
        row = (await s.scalars(select(OutboundEmail))).one()
        assert row.status is OutboundStatus.SENT and row.sent_at == mark.sent_at
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.SENT_TO_BANK
        assert (await events(s, case_id)).count("email.marked_sent") == 1


@pytest.mark.parametrize(
    ("offset", "message"),
    [(timedelta(hours=2), "in the future"), (timedelta(days=-2), "before the letter was approved")],
)
async def test_implausible_send_times_are_refused(
    sessions: async_sessionmaker[AsyncSession], offset: timedelta, message: str
) -> None:
    case_id, draft_id = await approved_draft(sessions)
    acts = sender(sessions, SANDBOX)
    summary = await run(acts.prepare_send, PrepareSendInput(case_id=case_id, draft_id=draft_id))
    with pytest.raises(ApplicationError, match=message):
        await run(
            acts.record_sent,
            RecordSentInput(
                case_id=case_id,
                email_id=summary.email_id,
                mark=MarkSent(sent_at=NOW + offset),
                now=NOW,
            ),
        )


async def test_real_send_is_blocked_until_bank_and_rules_are_verified(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, draft_id = await approved_draft(sessions)
    acts = sender(sessions, REAL)
    inp = PrepareSendInput(case_id=case_id, draft_id=draft_id)
    summary = await run(acts.prepare_send, inp)
    assert summary.status is OutboundStatus.BLOCKED
    assert any("not known" in r for r in summary.blocked_reasons)

    # The user picks their bank; retrying re-evaluates (still blocked: nothing verified yet).
    async with sessions.begin() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        case.bank_id = "pnb"
    again = await run(acts.prepare_send, inp)
    assert again.email_id == summary.email_id and again.status is OutboundStatus.BLOCKED
    assert any("Punjab National Bank" in r and "not verified" in r for r in again.blocked_reasons)
    assert any("rules" in r for r in again.blocked_reasons)
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(OutboundEmail)) == 1
        row = (await s.scalars(select(OutboundEmail))).one()
        assert row.recipient is None and row.bank_id == "pnb"
        assert (await events(s, case_id)).count("email.blocked") == 2

        with pytest.raises(ApplicationError, match="cannot be sent yet"):
            await run(
                acts.record_sent,
                RecordSentInput(
                    case_id=case_id, email_id=row.id, mark=MarkSent(sent_at=NOW), now=NOW
                ),
            )


async def test_unapproved_draft_cannot_be_sent(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id = await assessed_case(sessions)
    acts, _ = drafter(sessions, GOOD_FAILED.model_dump())
    summary = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    with pytest.raises(ApplicationError, match="approved"):
        await run(
            sender(sessions, SANDBOX).prepare_send,
            PrepareSendInput(case_id=case_id, draft_id=summary.draft_id),
        )
