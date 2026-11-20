"""Drafting and approval activities against Postgres (Neon test branch) with a FakeLLM.

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from recourse.agents.draft_graph import DRAFT_PURPOSE, MAX_ATTEMPTS, DraftStatus
from recourse.agents.draft_slots import CUSTOMER_NAME_PLACEHOLDER
from recourse.cases.review import Confirmation
from recourse.db.models import AuditEvent, Case, CaseStatus, DraftRecord, DraftRecordStatus
from recourse.llm.fake import FakeLLM
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    INVALID_USER_INPUT,
    AssessInput,
    ConfirmInput,
    DraftDecisionInput,
    DraftInput,
    RunIntakeInput,
)
from tests.db.test_case_activities import NOW, events, new_case_with_upload, run, sms_llm
from tests.unit.test_draft_graph import GOOD_FAILED

pytestmark = pytest.mark.db

LETTER_DATE = date(2026, 11, 17)
BAD_TEMPLATE = {"subject": "{{case.tag}} refund of Rs 2450", "body": "Pay me 2450 now."}


async def assessed_case(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, sms_llm())
    await run(acts.run_intake, RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW))
    await run(acts.confirm, ConfirmInput(case_id=case_id, confirmation=Confirmation(), now=NOW))
    await run(acts.assess, AssessInput(case_id=case_id, as_of=LETTER_DATE))
    return case_id


def drafter(
    sessions: async_sessionmaker[AsyncSession], *outputs: dict[str, Any]
) -> tuple[CaseActivities, FakeLLM]:
    llm = FakeLLM({DRAFT_PURPOSE: list(outputs)})
    return CaseActivities(sessions, llm), llm


async def draft_row(s: AsyncSession, draft_id: uuid.UUID) -> DraftRecord:
    row = await s.get(DraftRecord, draft_id)
    assert row is not None
    return row


async def test_draft_is_stored_and_the_case_awaits_approval(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await assessed_case(sessions)
    acts, llm = drafter(sessions, GOOD_FAILED.model_dump())
    inp = DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE)

    summary = await run(acts.draft, inp)
    assert summary.status is DraftStatus.DRAFTED
    again = await run(acts.draft, inp)  # a retry: same row, no second LLM call
    assert again == summary
    assert len(llm.calls_for(DRAFT_PURPOSE)) == 1

    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.AWAITING_APPROVAL
        row = await draft_row(s, summary.draft_id)
        assert row.status is DraftRecordStatus.PENDING
        assert row.body is not None and "₹2,450.00" in row.body
        assert CUSTOMER_NAME_PLACEHOLDER in row.body
        assert row.subject is not None and f"[{case.case_tag}]" in row.subject
        assert row.user_fill == ["customer.name"]
        assert (await events(s, case_id))[-1] == "draft.ready"


async def test_failed_draft_is_recorded_without_text(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await assessed_case(sessions)
    acts, _ = drafter(sessions, *[BAD_TEMPLATE] * MAX_ATTEMPTS)
    summary = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    assert summary.status is DraftStatus.FAILED
    async with sessions() as s:
        row = await draft_row(s, summary.draft_id)
        assert row.status is DraftRecordStatus.FAILED
        assert row.subject is None and row.body is None and row.errors
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.ASSESSED
        assert (await events(s, case_id))[-1] == "draft.failed"


async def approve_with(
    sessions: async_sessionmaker[AsyncSession], draft_id: uuid.UUID, body_edit: str | None = None
) -> None:
    """What the API does before signalling: store the final text on the row."""
    async with sessions.begin() as s:
        row = await s.get(DraftRecord, draft_id)
        assert row is not None and row.subject is not None and row.body is not None
        row.final_subject = row.subject
        row.final_body = (body_edit or row.body).replace(CUSTOMER_NAME_PLACEHOLDER, "Asha Verma")


async def test_approve_checks_the_stored_text_again(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await assessed_case(sessions)
    acts, _ = drafter(sessions, GOOD_FAILED.model_dump())
    summary = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    decision = DraftDecisionInput(case_id=case_id, draft_id=summary.draft_id, now=NOW)

    # Not yet filled in by the API → refused.
    with pytest.raises(ApplicationError) as e:
        await run(acts.approve_draft, decision)
    assert e.value.type == INVALID_USER_INPUT

    # Text changed behind the API's back (a wrong amount) → refused by the second check.
    async with sessions() as s:
        body = (await draft_row(s, summary.draft_id)).body
    assert body is not None
    await approve_with(sessions, summary.draft_id, body.replace("₹2,450.00", "₹9,450.00"))
    with pytest.raises(ApplicationError) as e:
        await run(acts.approve_draft, decision)
    assert "not one of this case's amounts" in str(e.value)

    await approve_with(sessions, summary.draft_id)
    await run(acts.approve_draft, decision)
    await run(acts.approve_draft, decision)  # idempotent
    async with sessions() as s:
        row = await draft_row(s, summary.draft_id)
        assert row.status is DraftRecordStatus.APPROVED and row.approved_at is not None
        assert row.final_body is not None and row.final_body.endswith("Asha Verma")
        evs = await events(s, case_id)
        assert evs.count("draft.approved") == 1


async def test_reject_then_redraft_uses_the_reason(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await assessed_case(sessions)
    acts, llm = drafter(sessions, GOOD_FAILED.model_dump(), GOOD_FAILED.model_dump())
    first = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    async with sessions.begin() as s:
        (await draft_row(s, first.draft_id)).reject_reason = "Please make it shorter."
    await run(
        acts.reject_draft, DraftDecisionInput(case_id=case_id, draft_id=first.draft_id, now=NOW)
    )
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.ASSESSED

    second = await run(acts.draft, DraftInput(case_id=case_id, seq=2, letter_date=LETTER_DATE))
    assert second.draft_id != first.draft_id
    prompt = llm.calls_for(DRAFT_PURPOSE)[1].prompt.user_text
    assert "Please make it shorter." in prompt

    # The old draft can no longer be approved or rejected.
    with pytest.raises(ApplicationError, match="no longer the current one"):
        await run(
            acts.approve_draft,
            DraftDecisionInput(case_id=case_id, draft_id=first.draft_id, now=NOW),
        )
    async with sessions() as s:
        rows = (await s.scalars(select(DraftRecord).order_by(DraftRecord.seq))).all()
        assert [r.status for r in rows] == [DraftRecordStatus.REJECTED, DraftRecordStatus.PENDING]


async def test_verify_rejections_are_kept_in_the_audit_event(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id = await assessed_case(sessions)
    acts, _ = drafter(sessions, BAD_TEMPLATE, GOOD_FAILED.model_dump())
    summary = await run(acts.draft, DraftInput(case_id=case_id, seq=1, letter_date=LETTER_DATE))
    assert summary.status is DraftStatus.DRAFTED
    async with sessions() as s:
        details = await s.scalar(
            select(AuditEvent.details).where(
                AuditEvent.case_id == case_id, AuditEvent.event_type == "draft.ready"
            )
        )
    assert details is not None and details["attempts"] == 2
    [rejected] = details["verify_rejections"]
    assert any("digit" in e for e in rejected)
