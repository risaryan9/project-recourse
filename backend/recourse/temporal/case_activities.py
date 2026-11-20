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

import re
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from temporalio import activity
from temporalio.exceptions import ApplicationError

from recourse.agents.draft_graph import DraftKind as GraphDraftKind
from recourse.agents.draft_graph import DraftStatus, run_draft
from recourse.agents.draft_slots import CUSTOMER_NAME_PLACEHOLDER, SlotError
from recourse.agents.intake_graph import IntakeInput, IntakeStatus, run_intake
from recourse.cases.assessment import AssessmentError, CaseDetails, assess, missing_details
from recourse.cases.drafts import InvalidEdit, latest_draft, recheck_final, slot_table_for
from recourse.cases.escalation import bank_type_for, days_until, escalation_for
from recourse.cases.review import InvalidAnswer, review
from recourse.config import Settings, get_settings
from recourse.db.audit import record_event
from recourse.db.models import (
    Actor,
    AuditEvent,
    Case,
    CaseStatus,
    DraftKind,
    DraftRecord,
    DraftRecordStatus,
    EntitlementRecord,
    EvidenceRecord,
    OutboundEmail,
    OutboundStatus,
    Upload,
)
from recourse.email.channel import (
    ChannelKind,
    EmailChannel,
    OutgoingEmail,
    resolve_recipient,
)
from recourse.email.contacts import BankContact, BankContacts, load_bank_contacts
from recourse.email.manual import ManualChannel
from recourse.evidence.models import IST
from recourse.evidence.validators import EvidenceCheck
from recourse.llm.base import Attachment, LLMClient, LLMError
from recourse.rules.models import Entitlement
from recourse.security.pii import RedactionKind
from recourse.temporal.case_types import (
    APPROVE_DRAFT,
    ASSESS,
    ASSESS_ESCALATION,
    CONFIRM,
    DRAFT,
    INVALID_USER_INPUT,
    MARK_ABANDONED,
    PREPARE_SEND,
    RECORD_SENT,
    REJECT_DRAFT,
    RUN_INTAKE,
    AssessInput,
    AssessSummary,
    ConfirmInput,
    ConfirmOutcome,
    DraftDecisionInput,
    DraftInput,
    DraftSummary,
    EscalationInput,
    EscalationSummary,
    IntakeSummary,
    MarkAbandonedInput,
    PrepareSendInput,
    RecordSentInput,
    RunIntakeInput,
    SendSummary,
)

LLM_FAILED = "LLMFailed"


def invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_USER_INPUT, non_retryable=True)


# How far a user-reported send time may lie outside [approval, now] (clock skew, slow clicks).
SENT_AT_TOLERANCE = timedelta(minutes=10)


class CaseActivities:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        *,
        settings: Settings | None = None,
        contacts: BankContacts | None = None,
        channels: dict[ChannelKind, EmailChannel] | None = None,
    ) -> None:
        self._sessions = sessions
        self._llm = llm
        self._settings = settings or get_settings()
        self._contacts = contacts or load_bank_contacts()
        self._channels: dict[ChannelKind, EmailChannel] = channels or {
            ChannelKind.MANUAL: ManualChannel()
        }

    def all(self) -> list[Any]:
        return [
            self.run_intake,
            self.confirm,
            self.assess,
            self.draft,
            self.approve_draft,
            self.reject_draft,
            self.prepare_send,
            self.record_sent,
            self.assess_escalation,
            self.mark_abandoned,
        ]

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

    # --- drafting ---------------------------------------------------------------------------------

    @activity.defn(name=DRAFT)
    async def draft(self, inp: DraftInput) -> DraftSummary:
        """Run the DraftGraph once and store the result as draft number `seq`.

        Idempotent: if draft `seq` already exists (an earlier attempt finished), return it
        without calling the LLM again. A rejected previous draft's reason becomes feedback.
        """
        async with self._sessions() as s:
            existing = await _draft_by_seq(s, inp.case_id, inp.seq)
            if existing is not None:
                return _draft_summary(existing)
            case = await s.get(Case, inp.case_id)
            if case is None:
                raise ApplicationError(f"case {inp.case_id} not found", non_retryable=True)
            evidence = (await _latest_evidence(s, case.id)).to_evidence()
            ent_row = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            if ent_row is None:
                raise invalid("The case has not been assessed yet.")
            entitlement = Entitlement.model_validate(ent_row.data)
            previous = await _draft_by_seq(s, case.id, inp.seq - 1) if inp.seq > 1 else None
            feedback = (
                previous.reject_reason
                if previous is not None and previous.status is DraftRecordStatus.REJECTED
                else None
            )
            case_tag = case.case_tag

        try:
            result = await run_draft(
                self._llm,
                entitlement,
                evidence,
                case_tag=case_tag,
                letter_date=inp.letter_date,
                feedback=feedback,
            )
        except SlotError as e:
            raise invalid(f"This case cannot be drafted: {e}") from e
        except LLMError as e:
            if e.retryable:
                raise
            raise ApplicationError(str(e), type=LLM_FAILED, non_retryable=True) from e

        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            existing = await _draft_by_seq(s, case.id, inp.seq)
            if existing is not None:  # a concurrent attempt won the race
                return _draft_summary(existing)
            drafted = result.status is DraftStatus.DRAFTED
            assert result.kind is GraphDraftKind.BANK_COMPLAINT
            row = DraftRecord(
                case_id=case.id,
                seq=inp.seq,
                kind=DraftKind.BANK_COMPLAINT,
                status=DraftRecordStatus.PENDING if drafted else DraftRecordStatus.FAILED,
                letter_date=inp.letter_date,
                attempts=result.attempts,
                rules_verified=result.rules_verified,
                subject=result.subject,
                body=result.body,
                template=result.template.model_dump() if result.template else None,
                slots_used=result.slots_used,
                user_fill=result.user_fill,
                errors=result.errors,
                models_used=result.models_used,
            )
            s.add(row)
            await s.flush()
            info: dict[str, Any] = {
                "draft_id": str(row.id),
                "seq": inp.seq,
                "attempts": result.attempts,
                "rules_verified": result.rules_verified,
                "with_feedback": feedback is not None,
                "models": result.models_used,
            }
            if drafted:
                # DRAFT_READY and AWAITING_APPROVAL happen together: nothing waits in between.
                case.status = CaseStatus.AWAITING_APPROVAL
                record_event(s, case.id, Actor.AGENT, "draft.ready", info)
            else:
                case.status = CaseStatus.ASSESSED
                record_event(
                    s, case.id, Actor.AGENT, "draft.failed", info | {"errors": result.errors}
                )
            return _draft_summary(row)

    @activity.defn(name=APPROVE_DRAFT)
    async def approve_draft(self, inp: DraftDecisionInput) -> None:
        """Approve the final text the user submitted (stored on the row by the API).

        The text is checked again here: the API's check gave the user a fast answer, this one
        is the decision. Idempotent: approving an approved draft does nothing.
        """
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            draft = await _current_draft_for_update(s, case.id, inp.draft_id)
            if draft.status is DraftRecordStatus.APPROVED:
                return
            if draft.status is not DraftRecordStatus.PENDING:
                raise invalid("This draft is not waiting for approval.")
            if draft.final_subject is None or draft.final_body is None:
                raise invalid("Fill in your name and approve the draft again.")
            try:
                table = await slot_table_for(s, case, draft)
                recheck_final(draft.final_subject, draft.final_body, table)
            except (InvalidEdit, SlotError) as e:
                raise invalid(f"The letter cannot be approved: {e}") from e
            draft.status = DraftRecordStatus.APPROVED
            draft.approved_at = inp.now
            record_event(
                s,
                case.id,
                Actor.USER,
                "draft.approved",
                {
                    "draft_id": str(draft.id),
                    "seq": draft.seq,
                    "edited_subject": _edited(draft.subject, draft.final_subject),
                    "edited_body": _edited(draft.body, draft.final_body),
                    "rules_verified": draft.rules_verified,
                },
            )

    @activity.defn(name=REJECT_DRAFT)
    async def reject_draft(self, inp: DraftDecisionInput) -> None:
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            draft = await _current_draft_for_update(s, case.id, inp.draft_id)
            if draft.status is DraftRecordStatus.REJECTED:
                return
            if draft.status is not DraftRecordStatus.PENDING:
                raise invalid("This draft is not waiting for approval.")
            draft.status = DraftRecordStatus.REJECTED
            case.status = CaseStatus.ASSESSED
            record_event(
                s,
                case.id,
                Actor.USER,
                "draft.rejected",
                {
                    "draft_id": str(draft.id),
                    "seq": draft.seq,
                    "with_reason": bool(draft.reject_reason),
                },
            )

    # --- sending ----------------------------------------------------------------------------------

    @activity.defn(name=PREPARE_SEND)
    async def prepare_send(self, inp: PrepareSendInput) -> SendSummary:
        """Apply the send policy to the approved draft and hand it to the channel.

        ManualChannel sends nothing: the email is PREPARED and the user sends it. Idempotent: a
        PREPARED or SENT email is returned as it is; a BLOCKED one is evaluated again (the user
        may have chosen their bank since).
        """
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            draft = await s.get(DraftRecord, inp.draft_id, with_for_update=True)
            if draft is None or draft.case_id != case.id:
                raise ApplicationError(f"draft {inp.draft_id} not found", non_retryable=True)
            if draft.status is not DraftRecordStatus.APPROVED:
                raise invalid("Only an approved draft can be sent.")
            assert draft.final_subject is not None and draft.final_body is not None
            row = await s.scalar(select(OutboundEmail).where(OutboundEmail.draft_id == draft.id))
            if row is not None and row.status is not OutboundStatus.BLOCKED:
                return _send_summary(row)

            evidence = (await _latest_evidence(s, case.id)).to_evidence()
            contact = self._bank_for(case.bank_id, evidence.payer_bank.value)
            recipient = resolve_recipient(
                sandbox=self._settings.email_sandbox,
                sandbox_recipient=self._settings.sandbox_recipient,
                contact=contact,
                rules_verified=draft.rules_verified,
            )
            channel = self._channels[ChannelKind.MANUAL]  # GmailChannel joins here (ADR-012)
            if row is None:
                row = OutboundEmail(case_id=case.id, draft_id=draft.id)
                s.add(row)
            row.channel = channel.kind.value
            row.sandbox = recipient.sandbox
            row.bank_id = contact.id if contact else None
            row.recipient = recipient.address
            row.subject = draft.final_subject
            row.body = draft.final_body
            row.blocked_reasons = recipient.blocked_reasons
            row.status = OutboundStatus.BLOCKED if recipient.blocked else OutboundStatus.PREPARED
            await s.flush()

            info: dict[str, Any] = {
                "email_id": str(row.id),
                "draft_id": str(draft.id),
                "channel": row.channel,
                "sandbox": row.sandbox,
                "bank_id": row.bank_id,
            }
            if recipient.blocked:
                record_event(
                    s,
                    case.id,
                    Actor.SYSTEM,
                    "email.blocked",
                    info | {"reasons": row.blocked_reasons},
                )
                return _send_summary(row)

            assert recipient.address is not None
            outcome = await channel.send(
                OutgoingEmail(
                    to=recipient.address,
                    subject=row.subject,
                    body=row.body,
                    sandbox=recipient.sandbox,
                )
            )
            if outcome.sent:  # a channel that sends by itself (Gmail)
                row.status = OutboundStatus.SENT
                row.sent_at = activity.info().current_attempt_scheduled_time
                row.message_id, row.thread_id = outcome.message_id, outcome.thread_id
                case.status = CaseStatus.SENT_TO_BANK
                record_event(s, case.id, Actor.SYSTEM, "email.sent", info)
            else:
                record_event(s, case.id, Actor.SYSTEM, "email.prepared", info)
            return _send_summary(row)

    @activity.defn(name=RECORD_SENT)
    async def record_sent(self, inp: RecordSentInput) -> None:
        """The user says they sent the email themselves (ManualChannel)."""
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            row = await s.get(OutboundEmail, inp.email_id, with_for_update=True)
            if row is None or row.case_id != case.id:
                raise ApplicationError(f"email {inp.email_id} not found", non_retryable=True)
            if row.status is OutboundStatus.SENT:
                return
            if row.status is not OutboundStatus.PREPARED:
                raise invalid("This email cannot be sent yet.")
            if inp.mark.channel.value != row.channel:
                raise invalid("This email is not sent through that channel.")
            draft = await s.get(DraftRecord, row.draft_id)
            assert draft is not None and draft.approved_at is not None
            sent_at = inp.mark.sent_at
            if sent_at.tzinfo is None:
                raise invalid("The send time needs a time zone.")
            if sent_at > inp.now + SENT_AT_TOLERANCE:
                raise invalid("The send time is in the future.")
            if sent_at < draft.approved_at - SENT_AT_TOLERANCE:
                raise invalid("The send time is before the letter was approved.")
            row.status = OutboundStatus.SENT
            row.sent_at = sent_at
            case.status = CaseStatus.SENT_TO_BANK
            record_event(
                s,
                case.id,
                Actor.USER,
                "email.marked_sent",
                {
                    "email_id": str(row.id),
                    "channel": row.channel,
                    "sandbox": row.sandbox,
                    "sent_at": sent_at.isoformat(),
                },
            )

    # --- the bank's reply window ------------------------------------------------------------------

    @activity.defn(name=ASSESS_ESCALATION)
    async def assess_escalation(self, inp: EscalationInput) -> EscalationSummary:
        """Work out the Ombudsman waiting period and filing deadline (rule engine) and store them.

        The complaint date is the day the approved complaint was sent (IST). Idempotent: the
        assessment is a pure function of the stored facts and `as_of`, and a retry finds its own
        audit event and does not record it twice.
        """
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            entitlement_row = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            if entitlement_row is None:
                raise ApplicationError("the case has not been assessed", non_retryable=True)
            entitlement = Entitlement.model_validate(entitlement_row.data)
            sent = await s.scalar(
                select(OutboundEmail)
                .where(
                    OutboundEmail.case_id == case.id,
                    OutboundEmail.status == OutboundStatus.SENT,
                )
                .order_by(OutboundEmail.sent_at)
                .limit(1)
            )
            if sent is None or sent.sent_at is None:
                raise invalid("The complaint has not been sent to the bank yet.")
            complained_on = sent.sent_at.astimezone(IST).date()

            evidence = (await _latest_evidence(s, case.id)).to_evidence()
            details = CaseDetails.model_validate(case.details)
            contact = self._bank_for(case.bank_id, evidence.payer_bank.value)
            bank_type = bank_type_for(details.bank_type, contact.bank_type if contact else None)
            assessment = escalation_for(
                entitlement,
                bank_type=bank_type,
                complained_on=complained_on,
                as_of=inp.as_of,
            )

            case.bank_complained_on = complained_on
            case.escalation = assessment.model_dump(mode="json")
            if inp.new_status is not None:
                case.status = inp.new_status
            info: dict[str, Any] = {
                "as_of": inp.as_of.isoformat(),
                "complained_on": complained_on.isoformat(),
                "bank_type": bank_type.value if bank_type else None,
                "entity_coverage": assessment.entity_coverage,
                "eligible_from": _iso(assessment.eligible_from),
                "filing_deadline": _iso(assessment.filing_deadline),
                "can_file_now": assessment.can_file_now,
            }
            if not await _has_event(s, case.id, inp.event, info):
                record_event(s, case.id, Actor.SYSTEM, inp.event, info)
            return EscalationSummary(
                entity_coverage=assessment.entity_coverage,
                can_file_now=assessment.can_file_now,
                eligible_from=assessment.eligible_from,
                filing_deadline=assessment.filing_deadline,
                wait_days=days_until(assessment.eligible_from, inp.as_of),
                needs_bank_type=bank_type is None,
            )

    def _bank_for(self, bank_id: str | None, extracted_name: str | None) -> BankContact | None:
        if bank_id is not None:
            return self._contacts.get(bank_id)
        return self._contacts.match(extracted_name)

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


async def _draft_by_seq(s: AsyncSession, case_id: uuid.UUID, seq: int) -> DraftRecord | None:
    draft: DraftRecord | None = await s.scalar(
        select(DraftRecord).where(DraftRecord.case_id == case_id, DraftRecord.seq == seq)
    )
    return draft


async def _current_draft_for_update(
    s: AsyncSession, case_id: uuid.UUID, draft_id: uuid.UUID
) -> DraftRecord:
    """The draft, locked, if it is the case's latest one."""
    latest = await latest_draft(s, case_id)
    if latest is None or latest.id != draft_id:
        raise invalid("That draft is no longer the current one.")
    draft = await s.get(DraftRecord, draft_id, with_for_update=True, populate_existing=True)
    assert draft is not None
    return draft


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


async def _has_event(
    s: AsyncSession, case_id: uuid.UUID, event_type: str, details: dict[str, Any]
) -> bool:
    """True if this exact audit event was already recorded (a retried activity)."""
    found = await s.scalar(
        select(AuditEvent.id)
        .where(
            AuditEvent.case_id == case_id,
            AuditEvent.event_type == event_type,
            AuditEvent.details == details,
        )
        .limit(1)
    )
    return found is not None


def _send_summary(row: OutboundEmail) -> SendSummary:
    return SendSummary(
        email_id=row.id,
        channel=ChannelKind(row.channel),
        status=row.status,
        sandbox=row.sandbox,
        blocked_reasons=row.blocked_reasons,
    )


def _draft_summary(row: DraftRecord) -> DraftSummary:
    return DraftSummary(
        draft_id=row.id,
        status=DraftStatus.FAILED
        if row.status is DraftRecordStatus.FAILED
        else DraftStatus.DRAFTED,
        rules_verified=row.rules_verified,
    )


def _edited(drafted: str | None, final: str) -> bool:
    """True if the user changed more than filling in their name."""
    if drafted is None:
        return True
    parts = [re.escape(p) for p in drafted.strip().split(CUSTOMER_NAME_PLACEHOLDER)]
    return re.fullmatch(".+?".join(parts), final, flags=re.S) is None


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
