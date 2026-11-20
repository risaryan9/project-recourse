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
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.agents.draft_graph import DISCLAIMER
from recourse.agents.draft_slots import EscalationContext, SlotError, format_date, format_inr
from recourse.agents.intake_graph import IntakeInput
from recourse.api.deps import get_runner, get_session, get_session_factory
from recourse.api.owner import Owner, require_case_owner
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
    BankReply,
    Case,
    CaseStatus,
    DraftKind,
    DraftRecord,
    DraftRecordStatus,
    EntitlementRecord,
    EvidenceRecord,
    OutboundEmail,
    OutboundStatus,
    ReplySource,
    ReplyStatus,
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
from recourse.rules.models import BankType, Entitlement
from recourse.security.pii import redact_text
from recourse.temporal.case_runner import CaseRunner
from recourse.temporal.case_types import (
    AbandonRequest,
    BankReplied,
    CaseInput,
    CloseRequest,
    Confirmation,
    MarkFiled,
    MarkResolved,
    MarkSent,
    ReplySummary,
    TimeScaleInput,
)

# Every /cases/{case_id}/... route answers 404 for a case the caller does not own.
router = APIRouter(prefix="/cases", tags=["cases"], dependencies=[Depends(require_case_owner)])
banks_router = APIRouter(prefix="/banks", tags=["banks"])

MAX_FILES = 5
MAX_REQUEST_BYTES = 20 * 1024 * 1024
ENDED = {CaseStatus.ABANDONED, CaseStatus.CLOSED, CaseStatus.RESOLVED}
# The complaint has reached the bank (the bank can no longer change).
SENT_OR_LATER = {
    CaseStatus.SENT_TO_BANK,
    CaseStatus.AWAITING_BANK,
    CaseStatus.BANK_REPLIED,
    CaseStatus.JUDGED,
    CaseStatus.ESCALATION_DRAFT_READY,
    CaseStatus.FILED_WITH_OMBUDSMAN,
}
# The case accepts bank replies and "I got my money".
WAITING_ON_BANK = {CaseStatus.AWAITING_BANK, CaseStatus.JUDGED}
MAX_REPLY_CHARS = 20_000

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
    txn_time_known: bool  # False: only the date was shown (do not display a time)


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
    bank_complaint_ref: str | None  # the bank's complaint number, once a reply states it
    last_reply: ReplySummary | None  # verdict on the latest bank reply (warnings included)
    can_escalate: bool  # show "Escalate to the RBI Ombudsman" / "Accept and close"
    # Escalation to the RBI Ombudsman
    needs_bank_type: bool  # ask for the bank's type (PUT /bank-type)
    can_recheck_escalation: bool
    awaiting_filing: bool  # the complaint is approved: file it on cms.rbi.org.in, then mark filed
    filing_deadline_passed: bool
    can_close: bool  # filed: report how it ended
    ombudsman_filed_at: datetime | None
    ombudsman_ref: str | None
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
    owner: Owner,
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
    case = await _create_case_rows(sessions, redacted, kinds, attachments, owner)

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


class NextDeadline(BaseModel):
    key: str
    label: str
    due: date


class CaseSummary(BaseModel):
    id: uuid.UUID
    case_tag: str
    status: CaseStatus
    created_at: datetime
    updated_at: datetime
    bank_name: str | None
    amount_inr: Decimal | None  # the disputed transaction (user-confirmed)
    total_owed_inr: Decimal | None  # rule engine, as of `owed_as_of`
    owed_as_of: date | None
    next_deadline: NextDeadline | None  # the earliest one not yet passed


@router.get("")
async def list_cases(owner: Owner, session: Session) -> list[CaseSummary]:
    """The caller's cases, newest first."""
    cases = list(
        await session.scalars(
            select(Case).where(Case.owner_id == owner).order_by(Case.created_at.desc()).limit(100)
        )
    )
    ids = [c.id for c in cases]
    entitlements = {
        e.case_id: e
        for e in await session.scalars(
            select(EntitlementRecord).where(EntitlementRecord.case_id.in_(ids))
        )
    }
    evidence: dict[uuid.UUID, EvidenceRecord] = {}
    for r in await session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.case_id.in_(ids))
        .order_by(EvidenceRecord.created_at)
    ):
        evidence[r.case_id] = r  # the latest wins
    today = datetime.now(IST).date()
    out = []
    for c in cases:
        ent = entitlements.get(c.id)
        ev = evidence[c.id].to_evidence() if c.id in evidence else None
        out.append(
            CaseSummary(
                id=c.id,
                case_tag=c.case_tag,
                status=c.status,
                created_at=c.created_at,
                updated_at=c.updated_at,
                bank_name=ev.payer_bank.value if ev else None,
                amount_inr=ev.amount_inr.value if ev else None,
                total_owed_inr=ent.total_owed_inr if ent else None,
                owed_as_of=ent.as_of if ent else None,
                next_deadline=None if c.status in ENDED else _next_deadline(c, ent, today),
            )
        )
    return out


def _next_deadline(case: Case, ent: EntitlementRecord | None, today: date) -> NextDeadline | None:
    """The earliest deadline from the rule engine (entitlement and escalation) still ahead."""
    lines: list[dict[str, Any]] = []
    if ent is not None:
        lines += ent.data.get("deadlines", [])
    if case.escalation is not None:
        lines += case.escalation.get("deadlines", [])
    ahead = [
        NextDeadline(key=d["key"], label=d["label"], due=date.fromisoformat(d["due"]))
        for d in lines
        if date.fromisoformat(d["due"]) >= today
    ]
    return min(ahead, key=lambda d: d.due, default=None)


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
        bank_complaint_ref=case.bank_complaint_ref,
        last_reply=live.last_reply if live else None,
        can_escalate=live.can_escalate if live else False,
        needs_bank_type=live.needs_bank_type if live else False,
        can_recheck_escalation=live.can_recheck_escalation if live else False,
        awaiting_filing=live.awaiting_filing if live else False,
        filing_deadline_passed=live.filing_deadline_passed if live else False,
        can_close=live.can_close if live else False,
        ombudsman_filed_at=case.ombudsman_filed_at,
        ombudsman_ref=case.ombudsman_ref,
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
    if case.status in ENDED:
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


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)
    received_at: datetime | None = None  # default: now
    sender: str | None = Field(default=None, max_length=320)  # the From address, if known


class ReplyCreated(BaseModel):
    id: uuid.UUID
    redactions: list[str]  # kinds of sensitive data removed before storing


class ReplyView(BaseModel):
    id: uuid.UUID
    source: ReplySource
    received_at: datetime
    sender: str | None
    text: str  # redacted
    status: ReplyStatus
    label: str | None
    next_action: str | None
    suspicious: bool | None
    verdict: dict[str, Any] | None


class BankTypeAnswer(BaseModel):
    bank_type: BankType


class FiledReport(BaseModel):
    filed_at: datetime | None = None  # default: now
    reference: str | None = Field(default=None, max_length=40)  # the CMS complaint number


class FilingField(BaseModel):
    label: str  # what the field is about (check the live form for its exact name)
    value: str


class OmbudsmanFiling(BaseModel):
    """Everything the user needs to file on cms.rbi.org.in, from the approved complaint and
    the rule engine. Recourse never files: the portal needs the customer (and a CAPTCHA)."""

    portal_url: str
    other_ways: list[str]
    fields: list[FilingField]
    title: str
    complaint_text: str
    filing_deadline: date | None
    filing_deadline_passed: bool
    disclaimer: str


class ResolvedReport(BaseModel):
    amount_received_inr: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


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
        if case.status in ENDED or case.status in SENT_OR_LATER:
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


@router.post("/{case_id}/bank-reply", status_code=status.HTTP_202_ACCEPTED)
async def add_bank_reply(
    case_id: uuid.UUID,
    reply: ReplyIn,
    runner: Runner,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> ReplyCreated:
    """The user pastes or forwards the bank's reply. Stored redacted; judged by the workflow."""
    now = datetime.now(IST)
    received_at = reply.received_at or now
    if received_at.tzinfo is None:
        raise HTTPException(422, "received_at needs a time zone")
    if received_at > now + timedelta(minutes=10):
        raise HTTPException(422, "received_at is in the future")
    if reply.sender is not None and "@" not in reply.sender:
        raise HTTPException(422, "sender must be an email address")
    text, kinds = redact_text(reply.text.strip())
    if not text:
        raise HTTPException(422, "the reply is empty")
    async with sessions.begin() as s:
        case = await s.get(Case, case_id, with_for_update=True)
        if case is None:
            raise HTTPException(404, "case not found")
        if case.status not in WAITING_ON_BANK:
            raise HTTPException(409, f"case is {case.status.value}, not waiting for the bank")
        email = await _latest_email(s, case_id)
        if email is not None and email.sent_at is not None and received_at < email.sent_at:
            raise HTTPException(422, "received_at is before the complaint was sent")
        row = BankReply(
            case_id=case.id,
            source=ReplySource.PASTE,
            received_at=received_at,
            sender=reply.sender.strip().lower() if reply.sender else None,
            body=text,
            redactions=sorted(k.value for k in kinds),
            status=ReplyStatus.PENDING,
        )
        s.add(row)
        await s.flush()
        record_event(
            s,
            case.id,
            Actor.BANK,
            "reply.received",
            {"reply_id": str(row.id), "source": row.source.value, "redactions": row.redactions},
        )
        reply_id, redactions = row.id, row.redactions
    await runner.bank_replied(case_id, BankReplied(reply_id=reply_id))
    return ReplyCreated(id=reply_id, redactions=redactions)


@router.get("/{case_id}/replies")
async def list_replies(case_id: uuid.UUID, session: Session) -> list[ReplyView]:
    await _get_case(session, case_id)
    rows = await session.scalars(
        select(BankReply).where(BankReply.case_id == case_id).order_by(BankReply.received_at)
    )
    return [
        ReplyView(
            id=r.id,
            source=r.source,
            received_at=r.received_at,
            sender=r.sender,
            text=r.body,
            status=r.status,
            label=r.label,
            next_action=r.next_action,
            suspicious=r.suspicious,
            verdict=r.verdict,
        )
        for r in rows
    ]


@router.post("/{case_id}/resolved", status_code=status.HTTP_202_ACCEPTED)
async def mark_resolved(
    case_id: uuid.UUID, report: ResolvedReport, session: Session, runner: Runner
) -> dict[str, str]:
    """The user received their money: close the case as RESOLVED."""
    case = await _get_case(session, case_id)
    if case.status not in WAITING_ON_BANK:
        raise HTTPException(409, f"case is {case.status.value}, not waiting for the bank")
    await runner.mark_resolved(
        case_id, MarkResolved(amount_received_inr=report.amount_received_inr)
    )
    return {"status": "resolving"}


@router.post("/{case_id}/escalate", status_code=status.HTTP_202_ACCEPTED)
async def escalate(
    case_id: uuid.UUID,
    runner: Runner,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> dict[str, str]:
    """The user finds the bank's reply unsatisfactory (RB-IOS 10(1)(f)) and escalates."""
    live = await runner.state(case_id)
    if live is None or not live.can_escalate or live.last_reply is None:
        raise HTTPException(409, "the case is not waiting for this decision")
    async with sessions.begin() as s:
        reply = await s.get(BankReply, live.last_reply.reply_id, with_for_update=True)
        if reply is None or reply.case_id != case_id:
            raise HTTPException(404, "reply not found")
        if not reply.unsatisfactory:
            reply.unsatisfactory = True
            record_event(
                s, case_id, Actor.USER, "reply.marked_unsatisfactory", {"reply_id": str(reply.id)}
            )
    await runner.escalate(case_id)
    return {"status": "escalating"}


@router.put("/{case_id}/bank-type", status_code=status.HTTP_202_ACCEPTED)
async def set_bank_type(
    case_id: uuid.UUID,
    answer: BankTypeAnswer,
    runner: Runner,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> dict[str, str]:
    """The user says what kind of bank it is (Ombudsman coverage depends on it)."""
    live = await runner.state(case_id)
    if live is None or not live.needs_bank_type:
        raise HTTPException(409, "the case is not asking for the bank's type")
    async with sessions.begin() as s:
        case = await s.get(Case, case_id, with_for_update=True)
        if case is None:
            raise HTTPException(404, "case not found")
        details = CaseDetails.model_validate(case.details)
        update = CaseDetails(bank_type=answer.bank_type)
        case.details = details.merged(update).model_dump(mode="json")
        record_event(
            s, case.id, Actor.USER, "case.bank_type_given", {"bank_type": answer.bank_type.value}
        )
    await runner.recheck_escalation(case_id)
    return {"status": "rechecking"}


@router.post("/{case_id}/escalation/recheck", status_code=status.HTTP_202_ACCEPTED)
async def recheck_escalation(case_id: uuid.UUID, runner: Runner) -> dict[str, str]:
    live = await runner.state(case_id)
    if live is None or not live.can_recheck_escalation:
        raise HTTPException(409, "the case is not waiting to recheck the escalation")
    await runner.recheck_escalation(case_id)
    return {"status": "rechecking"}


@router.get("/{case_id}/ombudsman-filing")
async def ombudsman_filing(case_id: uuid.UUID, session: Session) -> OmbudsmanFiling:
    case = await _get_case(session, case_id)
    draft = await latest_draft(session, case_id)
    if (
        draft is None
        or draft.kind is not DraftKind.OMBUDSMAN_COMPLAINT
        or draft.status is not DraftRecordStatus.APPROVED
        or draft.final_subject is None
        or draft.final_body is None
        or draft.context is None
    ):
        raise HTTPException(404, "no approved complaint to the RBI Ombudsman yet")
    ctx = EscalationContext.model_validate(draft.context)
    record = await _latest_evidence(session, case_id)
    ent_row = await session.scalar(
        select(EntitlementRecord).where(EntitlementRecord.case_id == case_id)
    )
    assert record is not None and ent_row is not None
    evidence = record.to_evidence()
    entitlement = Entitlement.model_validate(ent_row.data)
    contacts = load_bank_contacts()
    bank = contacts.get(case.bank_id) if case.bank_id else contacts.match(evidence.payer_bank.value)
    fields = [
        FilingField(
            label="Bank (regulated entity) complained against",
            value=bank.name if bank else (evidence.payer_bank.value or "-"),
        ),
        FilingField(
            label="Did you complain to the bank first? Date",
            value=f"Yes, on {format_date(ctx.complained_on)}",
        ),
        FilingField(label="Bank's complaint reference", value=ctx.complaint_ref or "-"),
        FilingField(
            label="Date of the bank's last reply",
            value=format_date(ctx.last_reply_on) if ctx.last_reply_on else "No reply",
        ),
        FilingField(
            label="Amount of the disputed transaction",
            value=format_inr(evidence.amount_inr.value) if evidence.amount_inr.value else "-",
        ),
    ]
    fields += [
        FilingField(label=f"Relief: {m.label}", value=format_inr(m.amount_inr))
        for m in entitlement.money
    ]
    fields.append(
        FilingField(label="Total relief sought", value=format_inr(entitlement.total_owed_inr))
    )
    deadline = ctx.assessment.filing_deadline
    return OmbudsmanFiling(
        portal_url="https://cms.rbi.org.in",
        other_ways=[
            "Email: crpc@rbi.org.in",
            "Post: Centralised Receipt and Processing Centre, Reserve Bank of India, Chandigarh",
        ],
        fields=fields,
        title=draft.final_subject,
        complaint_text=draft.final_body,
        filing_deadline=deadline,
        filing_deadline_passed=deadline is not None and datetime.now(IST).date() > deadline,
        disclaimer=DISCLAIMER,
    )


@router.post("/{case_id}/ombudsman/mark-filed", status_code=status.HTTP_202_ACCEPTED)
async def mark_filed(
    case_id: uuid.UUID, runner: Runner, report: FiledReport | None = None
) -> dict[str, str]:
    live = await runner.state(case_id)
    if live is None or not live.awaiting_filing:
        raise HTTPException(409, "there is no approved complaint waiting to be filed")
    now = datetime.now(IST)
    filed_at = (report.filed_at if report and report.filed_at else None) or now
    if filed_at.tzinfo is None:
        raise HTTPException(422, "filed_at needs a time zone")
    if filed_at > now + timedelta(minutes=10):
        raise HTTPException(422, "filed_at is in the future")
    reference = report.reference.strip() if report and report.reference else None
    try:
        mark = MarkFiled(filed_at=filed_at, reference=reference)
    except ValidationError as e:
        raise HTTPException(422, _short_errors(e)) from e
    await runner.mark_filed(case_id, mark)
    return {"status": "recording"}


@router.post("/{case_id}/close", status_code=status.HTTP_202_ACCEPTED)
async def close_case(case_id: uuid.UUID, request: CloseRequest, runner: Runner) -> dict[str, str]:
    live = await runner.state(case_id)
    if live is None or not live.can_close:
        raise HTTPException(409, "the case is not waiting for the Ombudsman's outcome")
    await runner.close(case_id, request)
    return {"status": "closing"}


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
    if case.status not in (CaseStatus.AWAITING_APPROVAL, CaseStatus.ESCALATION_DRAFT_READY):
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
    owner: str,
) -> _NewCase:
    for _ in range(5):  # retry on the (rare) case-tag collision
        try:
            async with sessions.begin() as s:
                case = Case(case_tag=new_case_tag(), owner_id=owner)
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
        txn_time_known=ev.txn_time_known,
    )


def _short_errors(e: ValidationError) -> str:
    """Field names and messages only (never the submitted values)."""
    return "; ".join(
        f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors()
    )
