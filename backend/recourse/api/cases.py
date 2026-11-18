"""Case endpoints.

    POST /cases                 text and/or files → new case, CaseWorkflow started (202)
    GET  /cases/{id}            status, facts, issues, entitlement, timeline
    POST /cases/{id}/confirm    the user's corrections / answers → `confirm` signal (202)
    POST /cases/{id}/abandon    → `abandon` signal (202)

The database is the source for what the page shows; the workflow's live state adds the last
error from a rejected answer. No authentication yet: anyone with a case ID can see the case
(local development only; accounts come before deployment).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.agents.intake_graph import IntakeInput
from recourse.api.deps import get_runner, get_session, get_session_factory
from recourse.cases.assessment import CaseDetails, missing_details
from recourse.cases.review import InvalidAnswer, review
from recourse.config import get_settings
from recourse.db.audit import record_event
from recourse.db.case_tag import new_case_tag
from recourse.db.models import (
    Actor,
    AuditEvent,
    Case,
    CaseStatus,
    EntitlementRecord,
    EvidenceRecord,
    Upload,
)
from recourse.db.models import (
    UploadFile as UploadFileRow,
)
from recourse.evidence.models import IST
from recourse.evidence.validators import EvidenceCheck, FieldIssue
from recourse.llm.base import Attachment
from recourse.security.pii import redact_text
from recourse.temporal.case_runner import CaseRunner
from recourse.temporal.case_types import AbandonRequest, CaseInput, Confirmation, TimeScaleInput

router = APIRouter(prefix="/cases", tags=["cases"])

MAX_FILES = 5
MAX_REQUEST_BYTES = 20 * 1024 * 1024
ENDED = {CaseStatus.ABANDONED, CaseStatus.CLOSED, CaseStatus.RESOLVED}

Session = Annotated[AsyncSession, Depends(get_session)]
Runner = Annotated[CaseRunner, Depends(get_runner)]


# =================================================================================================
# Response models
# =================================================================================================


class CaseCreated(BaseModel):
    id: uuid.UUID
    case_tag: str
    status: CaseStatus


class FactView(BaseModel):
    value: Any
    confidence: float
    source_text: str | None
    origin: str


class EvidenceView(BaseModel):
    source_kind: str
    extracted_by: str
    facts: dict[str, FactView]
    issues: list[FieldIssue]
    needs_confirmation: list[str]
    redactions: list[str]
    notes: list[str]


class TimelineEntry(BaseModel):
    occurred_at: datetime
    actor: Actor
    event_type: str
    details: dict[str, Any]


class CaseView(BaseModel):
    id: uuid.UUID
    case_tag: str
    status: CaseStatus
    created_at: datetime
    updated_at: datetime
    evidence: EvidenceView | None
    details: CaseDetails
    missing_details: list[str]
    entitlement: dict[str, Any] | None
    last_error: str | None  # e.g. "Please check: amount_inr" (from the workflow)
    timeline: list[TimelineEntry]


class ReviewPreview(BaseModel):
    """What the case will look like once the workflow applies the answers."""

    needs_confirmation: list[str]
    missing_details: list[str]
    ready: bool


# =================================================================================================
# Endpoints
# =================================================================================================


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_case(
    runner: Runner,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    text: Annotated[str | None, Form(max_length=20_000)] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> CaseCreated:
    attachments = await _read_files(files or [])
    try:
        IntakeInput(text=text, attachments=attachments)  # same limits the workflow will apply
    except ValidationError as e:
        raise HTTPException(422, _short_errors(e)) from e

    redacted, kinds = redact_text(text) if text else (None, set())
    case = await _create_case_rows(sessions, redacted, kinds, attachments)

    settings = get_settings()
    inp = CaseInput(
        case_id=case.id,
        upload_id=case.upload_id,
        time_scale=TimeScaleInput(
            demo_mode=settings.demo_mode, demo_day_seconds=settings.demo_day_seconds
        ),
    )
    try:
        await runner.start(inp)
    except Exception as e:
        # No workflow means nobody would ever process this case: remove it again.
        async with sessions.begin() as s:
            row = await s.get(Case, case.id)
            if row is not None:
                await s.delete(row)
        raise HTTPException(503, "could not start the case; please try again") from e
    return CaseCreated(id=case.id, case_tag=case.case_tag, status=CaseStatus.NEW)


@router.get("/{case_id}")
async def get_case(case_id: uuid.UUID, session: Session, runner: Runner) -> CaseView:
    case = await _get_case(session, case_id)
    record = await _latest_evidence(session, case_id)
    entitlement = await session.scalar(
        select(EntitlementRecord).where(EntitlementRecord.case_id == case_id)
    )
    events = await session.scalars(
        select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)
    )
    details = CaseDetails.model_validate(case.details)
    evidence_view = _evidence_view(record) if record else None
    missing = missing_details(record.to_evidence(), details) if record else []
    live = None if case.status in ENDED else await runner.state(case_id)
    return CaseView(
        id=case.id,
        case_tag=case.case_tag,
        status=case.status,
        created_at=case.created_at,
        updated_at=case.updated_at,
        evidence=evidence_view,
        details=details,
        missing_details=missing,
        entitlement=entitlement.data if entitlement else None,
        last_error=live.last_error if live else None,
        timeline=[
            TimelineEntry(
                occurred_at=e.occurred_at,
                actor=e.actor,
                event_type=e.event_type,
                details=e.details,
            )
            for e in events
        ],
    )


@router.post("/{case_id}/confirm", status_code=status.HTTP_202_ACCEPTED)
async def confirm_case(
    case_id: uuid.UUID, confirmation: Confirmation, session: Session, runner: Runner
) -> ReviewPreview:
    case = await _get_case(session, case_id)
    if case.status is not CaseStatus.EXTRACTED:
        raise HTTPException(409, f"case is {case.status.value}, not waiting for review")
    record = await _latest_evidence(session, case_id)
    if record is None:
        raise HTTPException(409, "case has no evidence yet")
    try:  # the workflow re-applies this; checking here gives the user an immediate answer
        result = review(
            record.to_evidence(),
            CaseDetails.model_validate(case.details),
            confirmation,
            now=datetime.now(IST),
        )
    except InvalidAnswer as e:
        raise HTTPException(422, {"message": str(e), "fields": e.fields}) from e
    await runner.confirm(case_id, confirmation)
    return ReviewPreview(
        needs_confirmation=result.needs_confirmation,
        missing_details=result.missing_details,
        ready=result.ready,
    )


@router.post("/{case_id}/abandon", status_code=status.HTTP_202_ACCEPTED)
async def abandon_case(
    case_id: uuid.UUID,
    session: Session,
    runner: Runner,
    request: AbandonRequest | None = None,
) -> dict[str, str]:
    case = await _get_case(session, case_id)
    if case.status in ENDED:
        raise HTTPException(409, f"case is already {case.status.value}")
    await runner.abandon(case_id, request or AbandonRequest())
    return {"status": "abandoning"}


# =================================================================================================
# Helpers
# =================================================================================================


class _NewCase(BaseModel):
    id: uuid.UUID
    case_tag: str
    upload_id: uuid.UUID


async def _read_files(files: list[UploadFile]) -> list[Attachment]:
    if len(files) > MAX_FILES:
        raise HTTPException(422, f"at most {MAX_FILES} files")
    attachments: list[Attachment] = []
    total = 0
    for f in files:
        data = await f.read(MAX_REQUEST_BYTES + 1)
        total += len(data)
        if total > MAX_REQUEST_BYTES:
            raise HTTPException(413, "upload too large (20 MB total)")
        try:
            attachments.append(
                Attachment.model_validate({"media_type": f.content_type, "data": data})
            )
        except ValidationError as e:
            raise HTTPException(422, f"{f.filename}: {_short_errors(e)}") from e
    return attachments


async def _create_case_rows(
    sessions: async_sessionmaker[AsyncSession],
    text: str | None,
    redactions: set[Any],
    attachments: list[Attachment],
) -> _NewCase:
    for _ in range(5):  # retry on the (rare) case-tag collision
        try:
            async with sessions.begin() as s:
                case = Case(case_tag=new_case_tag())
                s.add(case)
                await s.flush()
                upload = Upload(
                    case_id=case.id, text=text, redactions=sorted(r.value for r in redactions)
                )
                for i, a in enumerate(attachments):
                    upload.files.append(
                        UploadFileRow(position=i, media_type=a.media_type, data=a.data)
                    )
                s.add(upload)
                await s.flush()
                record_event(
                    s,
                    case.id,
                    Actor.USER,
                    "case.created",
                    {
                        "has_text": text is not None,
                        "files": [a.media_type for a in attachments],
                        "redactions": sorted(r.value for r in redactions),
                    },
                )
                return _NewCase(id=case.id, case_tag=case.case_tag, upload_id=upload.id)
        except IntegrityError:
            continue
    raise HTTPException(503, "could not allocate a case tag; please try again")


async def _get_case(session: AsyncSession, case_id: uuid.UUID) -> Case:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


async def _latest_evidence(session: AsyncSession, case_id: uuid.UUID) -> EvidenceRecord | None:
    record: EvidenceRecord | None = await session.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.case_id == case_id)
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    return record


def _evidence_view(record: EvidenceRecord) -> EvidenceView:
    ev = record.to_evidence()
    check = EvidenceCheck.model_validate(record.check_result)
    stored = ev.model_dump(mode="json")  # Decimal and datetime as strings
    facts = {name: FactView.model_validate(stored[name]) for name in ev.fact_names()}
    return EvidenceView(
        source_kind=ev.source_kind.value,
        extracted_by=ev.extracted_by,
        facts=facts,
        issues=check.issues,
        needs_confirmation=check.needs_user_confirmation,
        redactions=[r.value for r in ev.redactions],
        notes=ev.notes,
    )


def _short_errors(e: ValidationError) -> str:
    """Field names and messages only (never the submitted values)."""
    return "; ".join(
        f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors()
    )
