"""CaseWorkflow activities against Postgres (Neon test branch) with a scripted FakeLLM.

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from recourse.agents.intake_graph import CLASSIFY_PURPOSE, EXTRACT_PURPOSE, IntakeStatus
from recourse.cases.assessment import CaseDetails
from recourse.cases.review import Confirmation
from recourse.db.case_tag import new_case_tag
from recourse.db.models import (
    Actor,
    AuditEvent,
    Case,
    CaseStatus,
    EntitlementRecord,
    EvidenceRecord,
    Upload,
    UploadFile,
)
from recourse.evidence.models import IST, Evidence
from recourse.llm.base import LLMRefusalError, LLMUnavailableError
from recourse.llm.fake import FakeLLM
from recourse.rules.models import BankType
from recourse.security.pii import RedactionKind, redact_text
from recourse.temporal.case_activities import LLM_FAILED, CaseActivities
from recourse.temporal.case_types import (
    INVALID_USER_INPUT,
    AssessInput,
    ConfirmInput,
    MarkAbandonedInput,
    RunIntakeInput,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)
SMS = "Rs 2,450.00 debited via UPI on 14-11-26 18:05, UPI Ref No 631845207719. FAILED. OTP 482913"

NONE: dict[str, Any] = {"value": None, "confidence": 0.0, "source_text": None}


def fact(value: Any, confidence: float = 0.95) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "source_text": None}


def extraction(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {name: NONE for name in Evidence.fact_names()}
    data.update(
        case_type=fact("failed_transaction"),
        scenario=fact("BENEFICIARY_NOT_CREDITED"),
        channel=fact("UPI"),
        txn_status=fact("FAILED"),
        account_debited=fact(True),
        amount_inr=fact("Rs 2,450.00"),
        txn_timestamp=fact("2026-11-14T18:05:00"),
        is_domestic=fact(True),
        utr=fact("631845207719"),
        payer_bank=fact("Example Bank"),
        notes=[],
    )
    data.update(overrides)
    return data


def sms_llm(**overrides: Any) -> FakeLLM:
    return FakeLLM(
        {
            CLASSIFY_PURPOSE: [{"kind": "SMS", "reason": "A bank SMS."}],
            EXTRACT_PURPOSE: [extraction(**overrides)],
        }
    )


async def new_case_with_upload(
    sessions: async_sessionmaker[AsyncSession], text: str = SMS, png: bytes | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    redacted, kinds = redact_text(text)
    async with sessions.begin() as s:
        case = Case(case_tag=new_case_tag())
        s.add(case)
        await s.flush()
        upload = Upload(case_id=case.id, text=redacted, redactions=sorted(k.value for k in kinds))
        if png is not None:
            upload.files.append(UploadFile(position=0, media_type="image/png", data=png))
        s.add(upload)
        await s.flush()
        return case.id, upload.id


async def run(coro_fn: Any, arg: Any) -> Any:
    return await ActivityEnvironment().run(coro_fn, arg)


async def events(s: AsyncSession, case_id: uuid.UUID) -> list[str]:
    rows = await s.scalars(
        select(AuditEvent.event_type).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)
    )
    return list(rows)


async def test_intake_stores_evidence_and_deletes_the_upload(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, upload_id = await new_case_with_upload(sessions, png=b"\x89PNG fake")
    llm = sms_llm(utr=NONE)
    acts = CaseActivities(sessions, llm)

    summary = await run(
        acts.run_intake, RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW)
    )

    assert summary.status is IntakeStatus.NEEDS_CONFIRMATION
    assert summary.needs_confirmation == ["utr"]
    # The model saw the image and the redacted text, never the OTP.
    extract_call = llm.calls_for(EXTRACT_PURPOSE)[0]
    assert len(extract_call.request.attachments) == 1
    assert "482913" not in extract_call.prompt.user_text
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.EXTRACTED
        assert await s.get(Upload, upload_id) is None
        assert await s.scalar(select(func.count()).select_from(UploadFile)) == 0
        record = (await s.scalars(select(EvidenceRecord))).one()
        assert RedactionKind.SECRET_CODE in record.to_evidence().redactions
        assert await events(s, case_id) == ["intake.completed"]


async def test_intake_retry_returns_the_same_result(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, sms_llm())
    inp = RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW)

    first = await run(acts.run_intake, inp)
    second = await run(acts.run_intake, inp)  # no LLM answers left: must not call it again

    assert first == second
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(EvidenceRecord)) == 1
        assert await events(s, case_id) == ["intake.completed"]


async def test_not_payment_evidence_abandons_the_case(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, upload_id = await new_case_with_upload(sessions, text="recipe for dal")
    llm = FakeLLM({CLASSIFY_PURPOSE: [{"kind": "UNRELATED", "reason": "A recipe."}]})
    acts = CaseActivities(sessions, llm)
    inp = RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW)

    summary = await run(acts.run_intake, inp)
    assert summary.status is IntakeStatus.NOT_PAYMENT_EVIDENCE
    assert (await run(acts.run_intake, inp)) == summary  # idempotent
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.ABANDONED


async def test_llm_failures(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    inp = RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW)

    busy = CaseActivities(sessions, FakeLLM({CLASSIFY_PURPOSE: [LLMUnavailableError("busy")]}))
    with pytest.raises(LLMUnavailableError):  # retryable: Temporal tries again
        await run(busy.run_intake, inp)

    refused = CaseActivities(sessions, FakeLLM({CLASSIFY_PURPOSE: [LLMRefusalError("no")]}))
    with pytest.raises(ApplicationError) as exc:
        await run(refused.run_intake, inp)
    assert exc.value.type == LLM_FAILED and exc.value.non_retryable
    async with sessions() as s:  # nothing was consumed
        assert await s.get(Upload, upload_id) is not None


async def test_confirm_then_assess_failed_transaction(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, sms_llm(utr=NONE))
    await run(acts.run_intake, RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW))

    outcome = await run(
        acts.confirm,
        ConfirmInput(
            case_id=case_id,
            confirmation=Confirmation(corrections={"utr": "631845207719"}),
            now=NOW,
        ),
    )
    assert outcome.ready

    summary = await run(acts.assess, AssessInput(case_id=case_id, as_of=date(2026, 11, 17)))
    assert summary.covered
    assert summary.total_owed_inr == Decimal("2650.00")  # 2,450 refund + 2 days x 100

    again = await run(acts.assess, AssessInput(case_id=case_id, as_of=date(2026, 11, 17)))
    assert again == summary
    async with sessions() as s:
        case = await s.get(Case, case_id)
        assert case is not None and case.status is CaseStatus.ASSESSED
        ent = (await s.scalars(select(EntitlementRecord))).one()  # replaced, not duplicated
        assert ent.data["case_type"] == "failed_transaction"
        assert ent.data["citations"]
        record = (await s.scalars(select(EvidenceRecord))).one()
        assert record.to_evidence().utr.value == "631845207719"
        assert await events(s, case_id) == [
            "intake.completed",
            "case.facts_reviewed",
            "case.assessed",
            "case.assessed",
        ]


async def test_fraud_needs_bank_type(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, sms_llm(case_type=fact("fraud"), scenario=NONE))
    summary = await run(
        acts.run_intake, RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW)
    )
    assert summary.missing_details == ["bank_type"]

    with pytest.raises(ApplicationError) as exc:  # not ready: refuse to assess
        await run(acts.assess, AssessInput(case_id=case_id, as_of=date(2026, 11, 17)))
    assert exc.value.type == INVALID_USER_INPUT

    outcome = await run(
        acts.confirm,
        ConfirmInput(
            case_id=case_id,
            confirmation=Confirmation(details=CaseDetails(bank_type=BankType.COMMERCIAL_BANK)),
            now=NOW,
        ),
    )
    assert outcome.ready
    summary2 = await run(acts.assess, AssessInput(case_id=case_id, as_of=date(2026, 11, 17)))
    assert summary2.covered


async def test_invalid_correction_is_rejected_without_values(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, sms_llm())
    await run(acts.run_intake, RunIntakeInput(case_id=case_id, upload_id=upload_id, now=NOW))

    with pytest.raises(ApplicationError) as exc:
        await run(
            acts.confirm,
            ConfirmInput(
                case_id=case_id,
                confirmation=Confirmation(corrections={"amount_inr": "lots of money"}),
                now=NOW,
            ),
        )
    assert exc.value.type == INVALID_USER_INPUT
    assert exc.value.message == "Please check: amount_inr"


async def test_mark_abandoned_is_idempotent(sessions: async_sessionmaker[AsyncSession]) -> None:
    case_id, upload_id = await new_case_with_upload(sessions)
    acts = CaseActivities(sessions, FakeLLM())
    inp = MarkAbandonedInput(case_id=case_id, reason="user_abandoned", actor=Actor.USER)
    await run(acts.mark_abandoned, inp)
    await run(acts.mark_abandoned, inp)
    async with sessions() as s:
        assert await s.get(Upload, upload_id) is None  # pending upload cleaned up
        assert await events(s, case_id) == ["case.abandoned"]
