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
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from temporalio import activity
from temporalio.exceptions import ApplicationError

from recourse.agents.draft_graph import DraftKind as GraphDraftKind
from recourse.agents.draft_graph import DraftStatus, run_draft
from recourse.agents.draft_slots import (
    CUSTOMER_NAME_PLACEHOLDER,
    BankResponse,
    EscalationContext,
    SlotError,
)
from recourse.agents.intake_graph import IntakeInput, IntakeStatus, run_intake
from recourse.agents.reply_judge_graph import (
    NextAction,
    ReplyInput,
    ReplyLabel,
    ReplyVerdict,
    judge_reply,
)
from recourse.cases.assessment import AssessmentError, CaseDetails, assess, missing_details
from recourse.cases.drafts import InvalidEdit, latest_draft, recheck_final, slot_table_for
from recourse.cases.escalation import bank_type_for, days_until, escalation_for
from recourse.cases.review import InvalidAnswer, review
from recourse.config import Settings, get_settings
from recourse.db.audit import record_event
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
    GmailConnection,
    OutboundEmail,
    OutboundStatus,
    ReplySource,
    ReplyStatus,
    Upload,
)
from recourse.email.channel import (
    ChannelKind,
    EmailChannel,
    OutgoingEmail,
    resolve_recipient,
)
from recourse.email.contacts import BankContact, BankContacts, load_bank_contacts
from recourse.email.crypto import TokenCipher, TokenCipherError
from recourse.email.gmail import GmailApi, GmailChannel, GmailHttpError
from recourse.email.manual import ManualChannel
from recourse.email.mime import reply_text
from recourse.email.oauth import GoogleOAuth, OAuthError
from recourse.evidence.models import IST
from recourse.evidence.validators import EvidenceCheck
from recourse.llm.base import Attachment, LLMClient, LLMError
from recourse.llm.user_keys import NEEDS_KEY_MESSAGE, MissingUserKeys
from recourse.rules.models import Entitlement, EscalationAssessment
from recourse.security.pii import RedactionKind, redact_text
from recourse.temporal.case_types import (
    APPROVE_DRAFT,
    ASSESS,
    ASSESS_ESCALATION,
    CHECK_THREAD,
    CONFIRM,
    DRAFT,
    INVALID_USER_INPUT,
    JUDGE_REPLY,
    MARK_ABANDONED,
    PREPARE_SEND,
    RECORD_CLOSED,
    RECORD_FILED,
    RECORD_RESOLVED,
    RECORD_SENT,
    REJECT_DRAFT,
    RUN_INTAKE,
    SEND_EMAIL,
    AssessInput,
    AssessSummary,
    CheckThreadInput,
    ConfirmInput,
    ConfirmOutcome,
    DraftDecisionInput,
    DraftInput,
    DraftSummary,
    EscalationInput,
    EscalationSummary,
    IntakeSummary,
    JudgeReplyInput,
    MarkAbandonedInput,
    PrepareSendInput,
    RecordClosedInput,
    RecordFiledInput,
    RecordResolvedInput,
    RecordSentInput,
    ReplySummary,
    RunIntakeInput,
    SendEmailInput,
    SendSummary,
    ThreadSummary,
)
from recourse.tracing import case_trace

LLM_FAILED = "LLMFailed"
NEEDS_API_KEY = "NeedsApiKey"  # bring your own key: the owner has no key in the API right now


def invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_USER_INPUT, non_retryable=True)


# How far a user-reported send time may lie outside [approval, now] (clock skew, slow clicks).
SENT_AT_TOLERANCE = timedelta(minutes=10)


class CaseActivities:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LLMClient | None,
        *,
        llm_for: Callable[[uuid.UUID], Awaitable[LLMClient]] | None = None,
        settings: Settings | None = None,
        contacts: BankContacts | None = None,
        channels: dict[ChannelKind, EmailChannel] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._sessions = sessions
        self._llm = llm
        # Bring your own key (ADR-023): the client for a case, from its owner's key in the API.
        self._llm_for = llm_for
        self._settings = settings or get_settings()
        self._contacts = contacts or load_bank_contacts()
        self._channels: dict[ChannelKind, EmailChannel] = channels or {
            ChannelKind.MANUAL: ManualChannel()
        }
        # One HTTP client for Google (OAuth + Gmail); tests pass a mocked one.
        self._http = http

    async def _model(self, case_id: uuid.UUID) -> LLMClient:
        """The model client for this case: the owner's own key when keys are required."""
        if self._llm_for is not None:
            try:
                return await self._llm_for(case_id)
            except MissingUserKeys:
                raise ApplicationError(
                    NEEDS_KEY_MESSAGE, type=NEEDS_API_KEY, non_retryable=True
                ) from None
        assert self._llm is not None, "no model client configured"
        return self._llm

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
            self.send_email,
            self.check_thread,
            self.assess_escalation,
            self.judge_reply,
            self.record_resolved,
            self.record_filed,
            self.record_closed,
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
            with case_trace("intake", inp.case_id):
                result = await run_intake(await self._model(inp.case_id), intake, now=inp.now)
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
        if inp.kind is GraphDraftKind.OMBUDSMAN_COMPLAINT:
            await self._reassess(inp.case_id, inp.letter_date)
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
                if previous is not None
                and previous.status is DraftRecordStatus.REJECTED
                and previous.kind.value == inp.kind.value
                else None
            )
            case_tag = case.case_tag
            escalation = (
                await _escalation_context(s, case)
                if inp.kind is GraphDraftKind.OMBUDSMAN_COMPLAINT
                else None
            )

        try:
            with case_trace(f"draft:{inp.kind.value.lower()}", inp.case_id):
                result = await run_draft(
                    await self._model(inp.case_id),
                    entitlement,
                    evidence,
                    case_tag=case_tag,
                    letter_date=inp.letter_date,
                    feedback=feedback,
                    kind=inp.kind,
                    escalation=escalation,
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
            bank_letter = result.kind is GraphDraftKind.BANK_COMPLAINT
            row = DraftRecord(
                case_id=case.id,
                seq=inp.seq,
                kind=DraftKind(result.kind.value),
                context=escalation.model_dump(mode="json") if escalation else None,
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
                "kind": result.kind.value,
                "verify_rejections": result.rejected_attempts,
                "with_feedback": feedback is not None,
                "models": result.models_used,
            }
            if drafted:
                # DRAFT_READY and AWAITING_APPROVAL happen together: nothing waits in between.
                case.status = (
                    CaseStatus.AWAITING_APPROVAL
                    if bank_letter
                    else CaseStatus.ESCALATION_DRAFT_READY
                )
                record_event(s, case.id, Actor.AGENT, "draft.ready", info)
            else:
                if bank_letter:
                    case.status = CaseStatus.ASSESSED
                record_event(
                    s, case.id, Actor.AGENT, "draft.failed", info | {"errors": result.errors}
                )
            return _draft_summary(row)

    async def _reassess(self, case_id: uuid.UUID, as_of: date) -> None:
        """Bring the stored Entitlement up to `as_of` (compensation for delay keeps growing),
        so an escalation claims what is owed today. Approval rebuilds its slots from it."""
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, case_id)
            row = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            if row is None:
                raise invalid("The case has not been assessed yet.")
            if row.as_of >= as_of:
                return
            evidence = (await _latest_evidence(s, case.id)).to_evidence()
            try:
                entitlement = assess(
                    evidence, CaseDetails.model_validate(case.details), as_of=as_of
                )
            except AssessmentError as e:
                raise invalid(f"These facts cannot be assessed: {e}") from e
            previous = row.total_owed_inr
            row.as_of = as_of
            row.covered = entitlement.covered
            row.total_owed_inr = entitlement.total_owed_inr
            row.data = entitlement.model_dump(mode="json")
            record_event(
                s,
                case.id,
                Actor.SYSTEM,
                "case.reassessed",
                {
                    "as_of": as_of.isoformat(),
                    "total_owed_inr": str(entitlement.total_owed_inr),
                    "previous_total_owed_inr": str(previous),
                },
            )

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
            if draft.kind is DraftKind.BANK_COMPLAINT:
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
            # GmailChannel if this browser connected Gmail (and the server can use it); otherwise
            # ManualChannel. Gmail itself is called by `send_email`, outside this transaction.
            use_gmail = (
                self._gmail_ready()
                and await s.get(GmailConnection, case.owner_id or "") is not None
            )
            channel = self._channels[ChannelKind.MANUAL]
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
            if use_gmail:
                row.channel = ChannelKind.GMAIL.value
                info["channel"] = row.channel
                record_event(s, case.id, Actor.SYSTEM, "email.prepared", info)
                return _send_summary(row)
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
            # Genuine (not suspicious) judged replies count as the bank's communications.
            replies = list(
                await s.scalars(
                    select(BankReply).where(
                        BankReply.case_id == case.id,
                        BankReply.status == ReplyStatus.JUDGED,
                        BankReply.suspicious.is_(False),
                    )
                )
            )
            last = max((r.received_at.astimezone(IST).date() for r in replies), default=None)
            if last is not None:
                last = min(max(last, complained_on), inp.as_of)
            assessment = escalation_for(
                entitlement,
                bank_type=bank_type,
                complained_on=complained_on,
                as_of=inp.as_of,
                last_bank_communication=last,
                reply_unsatisfactory=last is not None and any(r.unsatisfactory for r in replies),
            )

            case.bank_complained_on = complained_on
            case.escalation = assessment.model_dump(mode="json")
            if inp.new_status is not None:
                case.status = inp.new_status
            info: dict[str, Any] = {
                "as_of": inp.as_of.isoformat(),
                "complained_on": complained_on.isoformat(),
                "last_bank_communication": _iso(last),
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

    @activity.defn(name=JUDGE_REPLY)
    async def judge_reply(self, inp: JudgeReplyInput) -> ReplySummary:
        """Judge one stored bank reply with the ReplyJudgeGraph and store the verdict.

        What is owed is re-assessed as of the day the reply arrived (compensation for delay
        keeps growing until the money is returned). Idempotent: a judged reply is returned as
        it is, without calling the LLM again.
        """
        async with self._sessions() as s:
            reply = await s.get(BankReply, inp.reply_id)
            if reply is None or reply.case_id != inp.case_id:
                raise ApplicationError(f"reply {inp.reply_id} not found", non_retryable=True)
            if reply.status is ReplyStatus.JUDGED:
                return _reply_summary(reply)
            case = await s.get(Case, inp.case_id)
            assert case is not None
            evidence = (await _latest_evidence(s, case.id)).to_evidence()
            details = CaseDetails.model_validate(case.details)
            ent_row = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            if ent_row is None:
                raise ApplicationError("the case has not been assessed", non_retryable=True)
            received_on = reply.received_at.astimezone(IST).date()
            try:
                entitlement = assess(evidence, details, as_of=max(received_on, ent_row.as_of))
            except AssessmentError:
                entitlement = Entitlement.model_validate(ent_row.data)
            contact = self._bank_for(case.bank_id, evidence.payer_bank.value)
            sent = await s.scalar(
                select(OutboundEmail).where(
                    OutboundEmail.case_id == case.id, OutboundEmail.status == OutboundStatus.SENT
                )
            )
            sandbox_reply = sent is not None and sent.sandbox
            judge_input = ReplyInput(
                text=reply.body,
                sender=reply.sender,
                **self._sender_policy(sandbox_reply, contact),
                entitlement=entitlement,
                disputed_amount_inr=evidence.amount_inr.value,
            )

        try:
            with case_trace("judge_reply", inp.case_id):
                verdict = await judge_reply(await self._model(inp.case_id), judge_input)
        except LLMError as e:
            if e.retryable:
                raise
            raise ApplicationError(str(e), type=LLM_FAILED, non_retryable=True) from e

        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            reply = await s.get(BankReply, inp.reply_id, with_for_update=True)
            assert reply is not None
            if reply.status is ReplyStatus.JUDGED:  # a concurrent attempt won the race
                return _reply_summary(reply)
            reply.status = ReplyStatus.JUDGED
            reply.verdict = verdict.model_dump(mode="json")
            reply.label = verdict.label.value
            reply.next_action = verdict.next_action.value
            reply.suspicious = verdict.suspicious
            reply.unsatisfactory = verdict.next_action is NextAction.ESCALATE
            if verdict.complaint_number and not verdict.suspicious:
                case.bank_complaint_ref = verdict.complaint_number
            if not inp.late:
                case.status = (
                    CaseStatus.JUDGED
                    if verdict.next_action in JUDGED_ACTIONS
                    else CaseStatus.AWAITING_BANK
                )
            info: dict[str, Any] = {
                "late": inp.late,
                "reply_id": str(reply.id),
                "label": verdict.label.value,
                "next_action": verdict.next_action.value,
                "suspicious": verdict.suspicious,
                "phishing_signals": verdict.phishing_signals,
                "injection_found": verdict.injection_found,
                "sender_check": verdict.sender_check.value,
                "owed_inr": str(verdict.owed_inr),
                "owed_basis": verdict.owed_basis.value,
                "paid_inr": str(verdict.paid_inr),
                "shortfall_inr": str(verdict.shortfall_inr),
                "models": verdict.models_used,
            }
            if verdict.suspicious:
                record_event(s, case.id, Actor.SYSTEM, "reply.suspicious", info)
            else:
                record_event(s, case.id, Actor.AGENT, "reply.judged", info)
            return _reply_summary(reply)

    @activity.defn(name=RECORD_RESOLVED)
    async def record_resolved(self, inp: RecordResolvedInput) -> None:
        """The user says they received their money: the case is RESOLVED.

        Records what was received next to what the rule engine says was owed. The raw upload
        is deleted (the extracted, redacted evidence stays). Idempotent.
        """
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            if case.status is CaseStatus.RESOLVED:
                return
            if case.status not in RESOLVABLE:
                raise invalid(f"A case that is {case.status.value} cannot be marked resolved.")
            ent_row = await s.scalar(
                select(EntitlementRecord).where(EntitlementRecord.case_id == case.id)
            )
            await s.execute(delete(Upload).where(Upload.case_id == case.id))
            case.status = CaseStatus.RESOLVED
            record_event(
                s,
                case.id,
                Actor.USER,
                "case.resolved",
                {
                    "amount_received_inr": str(inp.mark.amount_received_inr),
                    "owed_inr_at_assessment": str(ent_row.total_owed_inr) if ent_row else None,
                },
            )

    # --- escalation to the RBI Ombudsman ---------------------------------------------------------

    @activity.defn(name=RECORD_FILED)
    async def record_filed(self, inp: RecordFiledInput) -> None:
        """The user filed the approved Ombudsman complaint themselves. Idempotent."""
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            if case.status is CaseStatus.FILED_WITH_OMBUDSMAN:
                return
            if case.status is not CaseStatus.ESCALATION_DRAFT_READY:
                raise invalid(f"A case that is {case.status.value} cannot be marked filed.")
            draft = await s.get(DraftRecord, inp.draft_id)
            if (
                draft is None
                or draft.case_id != case.id
                or draft.kind is not DraftKind.OMBUDSMAN_COMPLAINT
                or draft.status is not DraftRecordStatus.APPROVED
                or draft.approved_at is None
            ):
                raise invalid("Approve the complaint to the RBI Ombudsman first.")
            filed_at = inp.mark.filed_at
            if filed_at.tzinfo is None:
                raise invalid("The filing time needs a time zone.")
            if filed_at > inp.now + SENT_AT_TOLERANCE:
                raise invalid("The filing time is in the future.")
            if filed_at < draft.approved_at - SENT_AT_TOLERANCE:
                raise invalid("The filing time is before the complaint was approved.")
            case.status = CaseStatus.FILED_WITH_OMBUDSMAN
            case.ombudsman_filed_at = filed_at
            case.ombudsman_ref = inp.mark.reference
            record_event(
                s,
                case.id,
                Actor.USER,
                "ombudsman.filed",
                {
                    "draft_id": str(draft.id),
                    "filed_at": filed_at.isoformat(),
                    "reference": inp.mark.reference,
                },
            )

    @activity.defn(name=RECORD_CLOSED)
    async def record_closed(self, inp: RecordClosedInput) -> None:
        """The Ombudsman complaint ended: the case is CLOSED. The raw upload is deleted."""
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            if case.status is CaseStatus.CLOSED:
                return
            if case.status is not CaseStatus.FILED_WITH_OMBUDSMAN:
                raise invalid(f"A case that is {case.status.value} cannot be closed.")
            await s.execute(delete(Upload).where(Upload.case_id == case.id))
            case.status = CaseStatus.CLOSED
            amount = inp.request.amount_received_inr
            record_event(
                s,
                case.id,
                Actor.USER,
                "case.closed",
                {
                    "outcome": inp.request.outcome.value,
                    "amount_received_inr": str(amount) if amount is not None else None,
                },
            )

    # --- GmailChannel -----------------------------------------------------------------------

    def _gmail_ready(self) -> bool:
        st = self._settings
        return bool(
            st.google_client_id
            and st.google_client_secret
            and st.google_redirect_uri
            and st.token_encryption_key
        )

    async def _gmail_channel(
        self, connection: GmailConnection, calls: list[tuple[str, dict[str, Any]]]
    ) -> GmailChannel:
        """A GmailChannel for this connection. Every Google call is appended to `calls`, which
        the caller writes to the case's audit trail."""
        oauth = GoogleOAuth(self._settings, client=self._http)
        refresh_token = TokenCipher.from_settings(self._settings).decrypt(
            connection.refresh_token_enc
        )
        cached: list[str] = []

        async def access_token() -> str:
            if not cached:
                try:
                    token, _ = await oauth.refresh(refresh_token)
                except OAuthError:
                    calls.append(("oauth.refresh", {"ok": False}))
                    raise
                calls.append(("oauth.refresh", {"ok": True}))
                cached.append(token)
            return cached[0]

        api = GmailApi(
            access_token,
            client=self._http,
            on_call=lambda endpoint, info: calls.append((endpoint, info)),
        )
        return GmailChannel(api, sender=connection.email)

    @staticmethod
    def _audit_gmail(
        s: AsyncSession, case_id: uuid.UUID, calls: list[tuple[str, dict[str, Any]]]
    ) -> None:
        for endpoint, info in calls:
            record_event(s, case_id, Actor.SYSTEM, f"gmail.{endpoint}", info)

    @activity.defn(name=SEND_EMAIL)
    async def send_email(self, inp: SendEmailInput) -> SendSummary:
        """Send an approved, PREPARED email through the user's Gmail.

        PREPARED -> SENDING (committed) -> Gmail -> SENT with its message and thread IDs. If a
        run dies after Gmail accepted the email but before SENT was saved, the row stays
        SENDING; a retry then refuses to send again unless the user checked and asked
        (`force`). Gmail unusable (not connected, access revoked) -> ManualChannel instead.
        """
        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            row = await s.get(OutboundEmail, inp.email_id, with_for_update=True)
            if row is None or row.case_id != case.id:
                raise ApplicationError(f"email {inp.email_id} not found", non_retryable=True)
            if row.status is OutboundStatus.SENT:
                return _send_summary(row)
            if row.channel != ChannelKind.GMAIL.value:
                raise invalid("This email is not sent through Gmail.")
            if row.status is OutboundStatus.SENDING and not inp.force:
                raise invalid(
                    "Gmail did not confirm the last attempt. Check your Gmail Sent folder; if "
                    "the letter isn't there, send it again."
                )
            if row.status not in (OutboundStatus.PREPARED, OutboundStatus.SENDING):
                raise invalid("This email cannot be sent yet.")
            assert row.recipient is not None
            connection = await s.get(GmailConnection, case.owner_id or "")
            if connection is None or not self._gmail_ready():
                return await self._fall_back_to_manual(s, case, row, "gmail_not_connected")
            row.status = OutboundStatus.SENDING
            email = OutgoingEmail(
                to=row.recipient, subject=row.subject, body=row.body, sandbox=row.sandbox
            )
            info: dict[str, Any] = {
                "email_id": str(row.id),
                "channel": row.channel,
                "sandbox": row.sandbox,
            }

        calls: list[tuple[str, dict[str, Any]]] = []
        try:
            channel = await self._gmail_channel(connection, calls)
            outcome = await channel.send(email)
        except (OAuthError, TokenCipherError):
            async with self._sessions.begin() as s:
                case = await _case_for_update(s, inp.case_id)
                row = await s.get(OutboundEmail, inp.email_id, with_for_update=True)
                assert row is not None
                self._audit_gmail(s, case.id, calls)
                return await self._fall_back_to_manual(s, case, row, "gmail_access_lost")
        except GmailHttpError as e:
            async with self._sessions.begin() as s:
                row = await s.get(OutboundEmail, inp.email_id, with_for_update=True)
                assert row is not None
                self._audit_gmail(s, inp.case_id, calls)
                if e.certainly_not_sent:
                    row.status = OutboundStatus.PREPARED
            if e.certainly_not_sent:
                raise invalid(f"Gmail refused to send the letter (HTTP {e.status}).") from e
            raise invalid(
                "Gmail did not confirm the letter was sent. Check your Gmail Sent folder; if it "
                "isn't there, send it again."
            ) from e
        except httpx.HTTPError as e:
            async with self._sessions.begin() as s:
                self._audit_gmail(s, inp.case_id, calls)
            raise invalid(
                "Gmail could not be reached, so it's not certain the letter was sent. Check your "
                "Gmail Sent folder; if it isn't there, send it again."
            ) from e

        async with self._sessions.begin() as s:
            case = await _case_for_update(s, inp.case_id)
            row = await s.get(OutboundEmail, inp.email_id, with_for_update=True)
            assert row is not None
            self._audit_gmail(s, case.id, calls)
            row.status = OutboundStatus.SENT
            row.sent_at = activity.info().current_attempt_scheduled_time
            row.message_id, row.thread_id = outcome.message_id, outcome.thread_id
            case.status = CaseStatus.SENT_TO_BANK
            record_event(
                s,
                case.id,
                Actor.SYSTEM,
                "email.sent",
                info | {"message_id": row.message_id, "thread_id": row.thread_id},
            )
            return _send_summary(row)

    async def _fall_back_to_manual(
        self, s: AsyncSession, case: Case, row: OutboundEmail, reason: str
    ) -> SendSummary:
        """Gmail can't be used: the same email is handed to the user (ManualChannel)."""
        row.channel = ChannelKind.MANUAL.value
        row.status = OutboundStatus.PREPARED
        record_event(
            s,
            case.id,
            Actor.SYSTEM,
            "email.fallback_manual",
            {"email_id": str(row.id), "reason": reason},
        )
        return _send_summary(row)

    @activity.defn(name=CHECK_THREAD)
    async def check_thread(self, inp: CheckThreadInput) -> ThreadSummary:
        """Read the email's Gmail thread (threads.get only) and store new messages from others.

        Each Gmail message is processed once (`bank_replies.gmail_message_id`). The letter
        itself and anything sent from the connected account are skipped. A stored reply keeps
        only what its sender wrote (the quoted letter is cut), PII-redacted, like a pasted one.
        """
        async with self._sessions() as s:
            case = await s.get(Case, inp.case_id)
            row = await s.get(OutboundEmail, inp.email_id)
            if case is None or row is None or row.case_id != case.id:
                raise ApplicationError("case or email not found", non_retryable=True)
            if row.thread_id is None or row.status is not OutboundStatus.SENT:
                return ThreadSummary(connected=False)
            connection = await s.get(GmailConnection, case.owner_id or "")
            if connection is None or not self._gmail_ready():
                return ThreadSummary(connected=False)
            seen = set(
                await s.scalars(
                    select(BankReply.gmail_message_id).where(
                        BankReply.case_id == case.id, BankReply.gmail_message_id.is_not(None)
                    )
                )
            )
            own = connection.email.lower()
            thread_id, sent_id = row.thread_id, row.message_id

        calls: list[tuple[str, dict[str, Any]]] = []
        try:
            channel = await self._gmail_channel(connection, calls)
            messages = await channel.get_thread(thread_id)
        except (OAuthError, TokenCipherError):
            async with self._sessions.begin() as s:
                self._audit_gmail(s, inp.case_id, calls)
            return ThreadSummary(connected=False)
        except (GmailHttpError, httpx.HTTPError):
            async with self._sessions.begin() as s:
                self._audit_gmail(s, inp.case_id, calls)
            raise  # transient: Temporal retries the check

        new_ids: list[uuid.UUID] = []
        async with self._sessions.begin() as s:
            self._audit_gmail(s, inp.case_id, calls)
            for m in messages:
                if m.id == sent_id or m.id in seen or m.sender == own:
                    continue
                text, kinds = redact_text(reply_text(m.text))
                if not text:
                    continue
                reply = BankReply(
                    case_id=inp.case_id,
                    source=ReplySource.GMAIL,
                    received_at=m.received_at,
                    sender=m.sender or None,
                    body=text,
                    redactions=sorted(k.value for k in kinds),
                    status=ReplyStatus.PENDING,
                    gmail_message_id=m.id,
                )
                s.add(reply)
                await s.flush()
                new_ids.append(reply.id)
                record_event(
                    s,
                    inp.case_id,
                    Actor.BANK,
                    "reply.received",
                    {"reply_id": str(reply.id), "source": "GMAIL", "redactions": reply.redactions},
                )
        return ThreadSummary(connected=True, new_reply_ids=new_ids, messages=len(messages))

    def _sender_policy(self, sandbox: bool, contact: BankContact | None) -> dict[str, Any]:
        """Who may write as "the bank". In the sandbox the "Sandbox Bank" is exactly the
        SANDBOX_RECIPIENT address; otherwise the bank's known sender domains."""
        if sandbox and self._settings.sandbox_recipient:
            return {
                "bank_domains": [],
                "trusted_senders": [self._settings.sandbox_recipient.lower()],
            }
        return {"bank_domains": list(contact.sender_domains) if contact else []}

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


async def _escalation_context(s: AsyncSession, case: Case) -> EscalationContext:
    """What the Ombudsman complaint needs: the bank complaint, how the bank responded (from the
    genuine judged replies, decided by code) and the latest escalation assessment."""
    if case.bank_complained_on is None or case.escalation is None:
        raise invalid("The bank's reply window has not been worked out yet.")
    assessment = EscalationAssessment.model_validate(case.escalation)
    if not assessment.can_file_now:
        flags = ", ".join(f.code.value for f in assessment.flags) or "not eligible yet"
        raise invalid(f"A complaint to the RBI Ombudsman cannot be filed now ({flags}).")
    replies = list(
        await s.scalars(
            select(BankReply)
            .where(
                BankReply.case_id == case.id,
                BankReply.status == ReplyStatus.JUDGED,
                BankReply.suspicious.is_(False),
            )
            .order_by(BankReply.received_at)
        )
    )
    substantive = [r for r in replies if r.label not in _NOT_A_DECISION]
    response = BankResponse.NO_REPLY
    if substantive:
        latest = ReplyVerdict.model_validate(substantive[-1].verdict)
        response = _RESPONSE_FOR_LABEL.get(latest.label, BankResponse.UNSATISFACTORY)
        if response is BankResponse.PARTIAL and latest.shortfall_inr == 0:
            response = BankResponse.UNSATISFACTORY
    return EscalationContext(
        complained_on=case.bank_complained_on,
        complaint_ref=case.bank_complaint_ref,
        bank_response=response,
        last_reply_on=replies[-1].received_at.astimezone(IST).date() if replies else None,
        assessment=assessment,
    )


_NOT_A_DECISION = {ReplyLabel.ACKNOWLEDGEMENT.value, ReplyLabel.NEEDS_INFO.value}
_RESPONSE_FOR_LABEL = {
    ReplyLabel.PARTIAL: BankResponse.PARTIAL,
    ReplyLabel.RESOLVED: BankResponse.PARTIAL,  # escalated: so it was short (checked above)
    ReplyLabel.REJECTED_NO_REASONS: BankResponse.REJECTED_NO_REASONS,
    ReplyLabel.REJECTED_WITH_REASONS: BankResponse.REJECTED_WITH_REASONS,
}


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


# The bank's window has started and the case is still open: "I got my money" is accepted.
RESOLVABLE = {
    CaseStatus.AWAITING_BANK,
    CaseStatus.JUDGED,
    CaseStatus.ESCALATION_DRAFT_READY,
    CaseStatus.FILED_WITH_OMBUDSMAN,
}

# Verdicts after which the case waits for the user (or escalates); the rest keep waiting.
JUDGED_ACTIONS = {NextAction.PROPOSE_RESOLVED, NextAction.ESCALATE, NextAction.ASK_USER}


def _reply_summary(row: BankReply) -> ReplySummary:
    assert row.verdict is not None
    verdict = ReplyVerdict.model_validate(row.verdict)
    return ReplySummary(
        reply_id=row.id,
        label=verdict.label,
        next_action=verdict.next_action,
        suspicious=verdict.suspicious,
        warnings=verdict.warnings,
        complaint_number=None if verdict.suspicious else verdict.complaint_number,
    )


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
