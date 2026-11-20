"""Case endpoints.

    POST /cases                 text and/or files → new case, CaseWorkflow started (202)
    GET  /cases/{id}            status, facts, issues, entitlement, timeline
    POST /cases/{id}/confirm    the user's corrections / answers → `confirm` signal (202)
    POST /cases/{id}/abandon    → `abandon` signal (202)
    GET  /cases/{id}/draft      the current draft (also inside GET /cases/{id})
    POST /cases/{id}/drafts/{draft_id}/approve   name + optional edits → `approve_draft` (202)
    POST /cases/{id}/drafts/{draft_id}/reject    optional reason → `reject_draft` (202)
    POST /cases/{id}/redraft    try again after a draft failed its checks → `redraft` (202)
    PUT  /cases/{id}/bank       the user picks their bank (when it could not be matched)
    GET  /cases/{id}/email      the outbound email: recipient, text, mailto link, sandbox flag
    POST /cases/{id}/email/mark-sent   the user sent it themselves → `mark_sent` (202)
    POST /cases/{id}/email/retry       after a blocked send was fixed → `retry_send` (202)
    GET  /banks                 the bank list for the picker

Draft decisions: the API checks the user's input, stores it on the draft row (so names, edited
text and reasons never enter Temporal history), then signals the workflow with the draft ID only.
The workflow's activity re-checks the stored text and makes the decision (ADR-011).

The database is the source for what the page shows; the workflow's live state adds the last
error from a rejected answer. No authentication yet: anyone with a case ID can see the case
(local development only; accounts come before deployment).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.agents.draft_graph import DISCLAIMER
from recourse.agents.draft_slots import SlotError
from recourse.agents.intake_graph import IntakeInput
from recourse.api.deps import get_runner, get_session, get_session_factory
from recourse.cases.assessment import CaseDetails, missing_details
from recourse.cases.drafts import (
    ApprovalEdits,
    InvalidEdit,
    Rejection,
    final_letter,
    latest_draft,
    slot_table_for,
)
from recourse.cases.review import InvalidAnswer, review
from recourse.config import get_settings
from recourse.db.audit import record_event
from recourse.db.case_tag import new_case_tag
from recourse.db.models import (
    Actor,
    AuditEvent,
    Case,
    CaseStatus,
    DraftRecord,
    DraftRecordStatus,
    EntitlementRecord,
    EvidenceRecord,
    OutboundEmail,
    OutboundStatus,
    Upload,
)
from recourse.db.models import (
    UploadFile as UploadFileRow,
)
from recourse.email.channel import ChannelKind
from recourse.email.contacts import BankContact, load_bank_contacts
from recourse.email.manual import mailto_link
from recourse.evidence.models import IST
from recourse.evidence.validators import EvidenceCheck, FieldIssue
from recourse.llm.base import Attachment
from recourse.security.pii import redact_text
from recourse.temporal.case_runner import CaseRunner
from recourse.temporal.case_types import (
    AbandonRequest,
    CaseInput,
    Confirmation,
    MarkSent,
    TimeScaleInput,
)

router = APIRouter(prefix="/cases", tags=["cases"])
banks_router = APIRouter(prefix="/banks", tags=["banks"])

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


class DraftView(BaseModel):
    id: uuid.UUID
    seq: int
    kind: str
    status: DraftRecordStatus
    subject: str | None
    body: str | None
    user_fill: list[str]  # placeholders the user fills in (e.g. "customer.name")
    rules_verified: bool  # False → real sending stays blocked until the rules are verified
    disclaimer: str
    final_subject: str | None
    final_body: str | None
    approved_at: datetime | None
    created_at: datetime


class BankView(BaseModel):
    id: str
    name: str
    has_email: bool
    complaint_form_url: str | None
    verified: bool


class EmailView(BaseModel):
    id: uuid.UUID
    channel: ChannelKind
    status: OutboundStatus
    sandbox: bool  # True → show "Sandbox — not sent to the real bank"
    bank: BankView | None
    to: str | None
    subject: str
    body: str
    mailto: str | None  # ManualChannel, when PREPARED
    blocked_reasons: list[str]
    sent_at: datetime | None


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
    draft: DraftView | None
    can_redraft: bool
    email: EmailView | None
    can_retry_send: bool
    bank: BankView | None  # chosen by the user, or matched from the extracted bank name
    # The bank's reply window: complaint date and the RBI Ombudsman escalation assessment
    # (rule engine; eligible-from date, filing deadline, citations).
    bank_complained_on: date | None
    escalation: dict[str, Any] | None
    bank_wait_expired: bool  # the bank did not reply within its window
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
    draft = await latest_draft(session, case_id)
    email = await _latest_email(session, case_id)
    contacts = load_bank_contacts()
    if case.bank_id is not None:
        bank = contacts.get(case.bank_id)
    else:
        bank = contacts.match(record.to_evidence().payer_bank.value) if record else None
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
        draft=_draft_view(draft) if draft else None,
        can_redraft=live.can_redraft if live else False,
        email=_email_view(email) if email else None,
        can_retry_send=live.can_retry_send if live else False,
        bank=_bank_view(bank) if bank else None,
        bank_complained_on=case.bank_complained_on,
        escalation=case.escalation,
        bank_wait_expired=live.bank_wait_expired if live else False,
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


@router.get("/{case_id}/draft")
async def get_draft(case_id: uuid.UUID, session: Session) -> DraftView:
    await _get_case(session, case_id)
    draft = await latest_draft(session, case_id)
    if draft is None:
        raise HTTPException(404, "no draft yet")
    return _draft_view(draft)


@router.post("/{case_id}/drafts/{draft_id}/approve", status_code=status.HTTP_202_ACCEPTED)
async def approve_draft(
    case_id: uuid.UUID,
    draft_id: uuid.UUID,
    edits: ApprovalEdits,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    runner: Runner,
) -> DraftView:
    async with sessions.begin() as s:
        case, draft = await _pending_draft(s, case_id, draft_id)
        assert draft.subject is not None and draft.body is not None
        try:
            table = await slot_table_for(s, case, draft)
            subject, body = final_letter(draft.subject, draft.body, edits, table)
        except InvalidEdit as e:
            raise HTTPException(
                422, {"message": "The letter cannot be approved.", "errors": e.errors}
            ) from e
        except SlotError as e:
            raise HTTPException(409, str(e)) from e
        draft.final_subject, draft.final_body = subject, body
        await s.flush()
        view = _draft_view(draft)
    await runner.approve_draft(case_id, draft_id)
    return view


@router.post("/{case_id}/drafts/{draft_id}/reject", status_code=status.HTTP_202_ACCEPTED)
async def reject_draft(
    case_id: uuid.UUID,
    draft_id: uuid.UUID,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    runner: Runner,
    rejection: Rejection | None = None,
) -> dict[str, str]:
    async with sessions.begin() as s:
        _, draft = await _pending_draft(s, case_id, draft_id)
        reason = (rejection.reason if rejection else "").strip()
        draft.reject_reason = redact_text(reason)[0] if reason else None
    await runner.reject_draft(case_id, draft_id)
    return {"status": "redrafting"}


@router.post("/{case_id}/redraft", status_code=status.HTTP_202_ACCEPTED)
async def redraft(case_id: uuid.UUID, session: Session, runner: Runner) -> dict[str, str]:
    case = await _get_case(session, case_id)
    if case.status is not CaseStatus.ASSESSED:
        raise HTTPException(409, f"case is {case.status.value}, not waiting for a new draft")
    live = await runner.state(case_id)
    if live is None or not live.can_redraft:
        raise HTTPException(409, "the case is not waiting for a new draft")
    await runner.redraft(case_id)
    return {"status": "redrafting"}


class BankChoice(BaseModel):
    bank_id: str


class SentReport(BaseModel):
    sent_at: datetime | None = None  # default: now


@router.put("/{case_id}/bank")
async def choose_bank(
    case_id: uuid.UUID,
    choice: BankChoice,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> BankView:
    contact = load_bank_contacts().get(choice.bank_id)
    if contact is None:
        raise HTTPException(422, "unknown bank")
    async with sessions.begin() as s:
        case = await s.get(Case, case_id, with_for_update=True)
        if case is None:
            raise HTTPException(404, "case not found")
        if case.status in ENDED or case.status is CaseStatus.SENT_TO_BANK:
            raise HTTPException(409, f"case is {case.status.value}; the bank can no longer change")
        case.bank_id = contact.id
        record_event(s, case.id, Actor.USER, "case.bank_selected", {"bank_id": contact.id})
    return _bank_view(contact)


@router.get("/{case_id}/email")
async def get_email(case_id: uuid.UUID, session: Session) -> EmailView:
    await _get_case(session, case_id)
    email = await _latest_email(session, case_id)
    if email is None:
        raise HTTPException(404, "no email yet")
    return _email_view(email)


@router.post("/{case_id}/email/mark-sent", status_code=status.HTTP_202_ACCEPTED)
async def mark_sent(
    case_id: uuid.UUID, session: Session, runner: Runner, report: SentReport | None = None
) -> dict[str, str]:
    await _get_case(session, case_id)
    email = await _latest_email(session, case_id)
    if email is None or email.status is not OutboundStatus.PREPARED:
        raise HTTPException(409, "there is no email waiting to be sent")
    if email.channel != ChannelKind.MANUAL.value:
        raise HTTPException(409, "this email is sent automatically")
    now = datetime.now(IST)
    sent_at = (report.sent_at if report and report.sent_at else None) or now
    if sent_at.tzinfo is None:
        raise HTTPException(422, "sent_at needs a time zone")
    if sent_at > now + timedelta(minutes=10):
        raise HTTPException(422, "sent_at is in the future")
    await runner.mark_sent(case_id, MarkSent(channel=ChannelKind.MANUAL, sent_at=sent_at))
    return {"status": "recording"}


@router.post("/{case_id}/email/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_send(case_id: uuid.UUID, session: Session, runner: Runner) -> dict[str, str]:
    await _get_case(session, case_id)
    live = await runner.state(case_id)
    if live is None or not live.can_retry_send:
        raise HTTPException(409, "the case is not waiting to retry sending")
    await runner.retry_send(case_id)
    return {"status": "retrying"}


@banks_router.get("")
async def list_banks() -> list[BankView]:
    return [_bank_view(b) for b in load_bank_contacts().banks]


# =================================================================================================
# Helpers
# =================================================================================================


async def _latest_email(session: AsyncSession, case_id: uuid.UUID) -> OutboundEmail | None:
    email: OutboundEmail | None = await session.scalar(
        select(OutboundEmail)
        .where(OutboundEmail.case_id == case_id)
        .order_by(OutboundEmail.created_at.desc())
        .limit(1)
    )
    return email


def _bank_view(b: BankContact) -> BankView:
    return BankView(
        id=b.id,
        name=b.name,
        has_email=b.grievance_email is not None,
        complaint_form_url=b.complaint_form_url,
        verified=b.verified,
    )


def _email_view(e: OutboundEmail) -> EmailView:
    contact = load_bank_contacts().get(e.bank_id) if e.bank_id else None
    manual_ready = (
        e.channel == ChannelKind.MANUAL.value
        and e.status is OutboundStatus.PREPARED
        and e.recipient is not None
    )
    return EmailView(
        id=e.id,
        channel=ChannelKind(e.channel),
        status=e.status,
        sandbox=e.sandbox,
        bank=_bank_view(contact) if contact else None,
        to=e.recipient,
        subject=e.subject,
        body=e.body,
        mailto=mailto_link(e.recipient, e.subject, e.body)
        if manual_ready and e.recipient
        else None,
        blocked_reasons=e.blocked_reasons,
        sent_at=e.sent_at,
    )


async def _pending_draft(
    s: AsyncSession, case_id: uuid.UUID, draft_id: uuid.UUID
) -> tuple[Case, DraftRecord]:
    """The case and its current draft, locked, if that draft is waiting for approval."""
    case = await s.get(Case, case_id, with_for_update=True)
    if case is None:
        raise HTTPException(404, "case not found")
    if case.status is not CaseStatus.AWAITING_APPROVAL:
        raise HTTPException(409, f"case is {case.status.value}, not waiting for approval")
    latest = await latest_draft(s, case_id)
    if latest is None or latest.id != draft_id:
        raise HTTPException(409, "that draft is no longer the current one")
    draft = await s.get(DraftRecord, draft_id, with_for_update=True, populate_existing=True)
    assert draft is not None
    if draft.status is not DraftRecordStatus.PENDING:
        raise HTTPException(409, f"draft is {draft.status.value}, not waiting for approval")
    return case, draft


def _draft_view(d: DraftRecord) -> DraftView:
    return DraftView(
        id=d.id,
        seq=d.seq,
        kind=d.kind.value,
        status=d.status,
        subject=d.subject,
        body=d.body,
        user_fill=d.user_fill,
        rules_verified=d.rules_verified,
        disclaimer=DISCLAIMER,
        final_subject=d.final_subject,
        final_body=d.final_body,
        approved_at=d.approved_at,
        created_at=d.created_at,
    )


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
