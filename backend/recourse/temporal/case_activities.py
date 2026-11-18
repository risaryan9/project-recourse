"""CaseWorkflow activities: the steps with side effects (LLM calls, database writes).

Each activity writes its result, the new case status and an audit event in **one database
transaction**, and is **idempotent**: Temporal may run it again after a crash or timeout, and a
second run returns the same answer without duplicating anything.

Errors:
* bad user input and permanent LLM failures → `ApplicationError(non_retryable=True)`, so the
  workflow handles them instead of retrying;
* everything else (network, database, overloaded LLM) → raised as-is, so Temporal retries.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from temporalio import activity
from temporalio.exceptions import ApplicationError

from recourse.agents.intake_graph import IntakeInput, IntakeStatus, run_intake
from recourse.cases.assessment import AssessmentError, CaseDetails, assess, missing_details
from recourse.cases.review import InvalidAnswer, review
from recourse.db.audit import record_event
from recourse.db.models import (
    Actor,
    Case,
    CaseStatus,
    EntitlementRecord,
    EvidenceRecord,
    Upload,
)
from recourse.evidence.validators import EvidenceCheck
from recourse.llm.base import Attachment, LLMClient, LLMError
from recourse.security.pii import RedactionKind
from recourse.temporal.case_types import (
    ASSESS,
    CONFIRM,
    INVALID_USER_INPUT,
    MARK_ABANDONED,
    RUN_INTAKE,
    AssessInput,
    AssessSummary,
    ConfirmInput,
    ConfirmOutcome,
    IntakeSummary,
    MarkAbandonedInput,
    RunIntakeInput,
)

LLM_FAILED = "LLMFailed"


def invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_USER_INPUT, non_retryable=True)


class CaseActivities:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], llm: LLMClient) -> None:
        self._sessions = sessions
        self._llm = llm

    def all(self) -> list[Any]:
        return [self.run_intake, self.confirm, self.assess, self.mark_abandoned]

    # --- intake ---------------------------------------------------------------------------------

    @activity.defn(name=RUN_INTAKE)
    async def run_intake(self, inp: RunIntakeInput) -> IntakeSummary:
        async with self._sessions() as s:
            upload = await s.get(Upload, inp.upload_id, options=[selectinload(Upload.files)])
            if upload is None:  # an earlier attempt already finished
                return await _existing_intake(s, inp.case_id)
            intake = IntakeInput(
                text=upload.text,
                attachments=[
                    Attachment(media_type=f.media_type, data=f.data) for f in upload.files
                ],
                prior_redactions=[RedactionKind(r) for r in upload.redactions],
            )

        try:
            result = await run_intake(self._llm, intake, now=inp.now)
        except LLMError as e:
            if e.retryable:
                raise  # Temporal retries later
            raise ApplicationError(str(e), type=LLM_FAILED, non_retryable=True) from e

        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            still_there = await s.get(Upload, inp.upload_id, with_for_update=True)
            if still_there is None:  # a concurrent attempt won the race
                return await _existing_intake(s, inp.case_id)
            await s.delete(still_there)

            if result.status is IntakeStatus.NOT_PAYMENT_EVIDENCE:
                case.status = CaseStatus.ABANDONED
                record_event(
                    s,
                    case.id,
                    Actor.AGENT,
                    "intake.not_payment_evidence",
                    {"models": result.models_used},
                )
                return IntakeSummary(status=result.status)

            record = EvidenceRecord.from_intake(case.id, result)
            s.add(record)
            case.status = CaseStatus.EXTRACTED
            assert result.evidence is not None
            missing = missing_details(result.evidence, CaseDetails.model_validate(case.details))
            record_event(
                s,
                case.id,
                Actor.AGENT,
                "intake.completed",
                {
                    "status": result.status.value,
                    "source_kind": result.evidence.source_kind.value,
                    "needs_confirmation": result.needs_user_confirmation,
                    "missing_details": missing,
                    "redactions": [r.value for r in result.evidence.redactions],
                    "models": result.models_used,
                },
            )
            return IntakeSummary(
                status=result.status,
                needs_confirmation=result.needs_user_confirmation,
                missing_details=missing,
            )

    # --- review -----------------------------------------------------------------------------------

    @activity.defn(name=CONFIRM)
    async def confirm(self, inp: ConfirmInput) -> ConfirmOutcome:
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            record = await _latest_evidence(s, case.id)
            try:
                result = review(
                    record.to_evidence(),
                    CaseDetails.model_validate(case.details),
                    inp.confirmation,
                    now=inp.now,
                )
            except InvalidAnswer as e:
                raise invalid(str(e)) from e

            assert result.intake.check is not None
            record.data = result.evidence.model_dump(mode="json")
            record.check_result = result.intake.check.model_dump(mode="json")
            record.intake_status = result.intake.status
            case.details = result.details.model_dump(mode="json")
            record_event(
                s,
                case.id,
                Actor.USER,
                "case.facts_reviewed",
                {
                    "corrected_fields": sorted(inp.confirmation.corrections),
                    "answered_details": sorted(
                        inp.confirmation.details.model_dump(exclude_unset=True)
                        if inp.confirmation.details
                        else []
                    ),
                    "needs_confirmation": result.needs_confirmation,
                    "missing_details": result.missing_details,
                },
            )
            return ConfirmOutcome(
                needs_confirmation=result.needs_confirmation,
                missing_details=result.missing_details,
            )

    # --- assessment -------------------------------------------------------------------------------

    @activity.defn(name=ASSESS)
    async def assess(self, inp: AssessInput) -> AssessSummary:
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            record = await _latest_evidence(s, case.id)
            evidence = record.to_evidence()
            details = CaseDetails.model_validate(case.details)
            check = EvidenceCheck.model_validate(record.check_result)
            if not check.ok or missing_details(evidence, details):
                raise invalid("The facts are not fully confirmed yet.")
            try:
                entitlement = assess(evidence, details, as_of=inp.as_of)
            except AssessmentError as e:
                raise invalid(f"These facts cannot be assessed: {e}") from e

            existing = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            row = existing or EntitlementRecord(case_id=case.id)
            row.as_of = inp.as_of
            row.covered = entitlement.covered
            row.total_owed_inr = entitlement.total_owed_inr
            row.data = entitlement.model_dump(mode="json")
            if existing is None:
                s.add(row)
            case.status = CaseStatus.ASSESSED
            record_event(
                s,
                case.id,
                Actor.SYSTEM,
                "case.assessed",
                {
                    "case_type": entitlement.case_type,
                    "covered": entitlement.covered,
                    "not_covered_reason": entitlement.not_covered_reason,
                    "regime_id": entitlement.regime_id,
                    "rule_id": entitlement.rule_id,
                    "total_owed_inr": str(entitlement.total_owed_inr),
                    "flags": [f.code.value for f in entitlement.flags],
                    "as_of": inp.as_of.isoformat(),
                },
            )
            return AssessSummary(
                covered=entitlement.covered, total_owed_inr=entitlement.total_owed_inr
            )

    # --- end -------------------------------------------------------------------------------------

    @activity.defn(name=MARK_ABANDONED)
    async def mark_abandoned(self, inp: MarkAbandonedInput) -> None:
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            await s.execute(delete(Upload).where(Upload.case_id == case.id))
            if case.status is CaseStatus.ABANDONED:
                return
            case.status = CaseStatus.ABANDONED
            record_event(s, case.id, inp.actor, "case.abandoned", {"reason": inp.reason})


# --- helpers -----------------------------------------------------------------------------------


async def _case_for_update(s: AsyncSession, case_id: uuid.UUID) -> Case:
    case = await s.get(Case, case_id, with_for_update=True)
    if case is None:
        raise ApplicationError(f"case {case_id} not found", non_retryable=True)
    return case


async def _latest_evidence(s: AsyncSession, case_id: uuid.UUID) -> EvidenceRecord:
    record = await s.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.case_id == case_id)
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    if record is None:
        raise ApplicationError(f"case {case_id} has no evidence", non_retryable=True)
    return record


async def _existing_intake(s: AsyncSession, case_id: uuid.UUID) -> IntakeSummary:
    """Rebuild the intake result from the database (for a retried, already-finished intake)."""
    case = await s.get(Case, case_id)
    if case is None:
        raise ApplicationError(f"case {case_id} not found", non_retryable=True)
    record = await s.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.case_id == case_id)
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    if record is None:
        if case.status is CaseStatus.ABANDONED:
            return IntakeSummary(status=IntakeStatus.NOT_PAYMENT_EVIDENCE)
        raise ApplicationError(f"upload for case {case_id} not found", non_retryable=True)
    check = EvidenceCheck.model_validate(record.check_result)
    return IntakeSummary(
        status=record.intake_status,
        needs_confirmation=check.needs_user_confirmation,
        missing_details=missing_details(
            record.to_evidence(), CaseDetails.model_validate(case.details)
        ),
    )
