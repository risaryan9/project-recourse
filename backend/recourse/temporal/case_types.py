"""Inputs and outputs of the CaseWorkflow, its signals, queries and activities.

These cross the Temporal boundary (serialised with the Pydantic data converter) and are stored
in workflow history, so they carry IDs, statuses and field names, plus the user's corrected
values (already PII-safe facts). Never raw uploads.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from recourse.agents.draft_graph import DraftStatus
from recourse.agents.intake_graph import IntakeStatus
from recourse.cases.review import Confirmation as Confirmation  # the confirm signal payload
from recourse.db.models import Actor, CaseStatus, OutboundStatus
from recourse.email.channel import ChannelKind

# Activity names (stable: they are recorded in workflow history).
RUN_INTAKE = "case.run_intake"
CONFIRM = "case.confirm"
ASSESS = "case.assess"
MARK_ABANDONED = "case.mark_abandoned"
DRAFT = "case.draft"
APPROVE_DRAFT = "case.approve_draft"
REJECT_DRAFT = "case.reject_draft"
PREPARE_SEND = "case.prepare_send"
RECORD_SENT = "case.record_sent"

# Error type for ApplicationErrors caused by bad user input (never retried).
INVALID_USER_INPUT = "InvalidUserInput"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeScaleInput(_Model):
    """Captured outside the workflow (settings), so timers replay identically."""

    demo_mode: bool = False
    demo_day_seconds: int = Field(default=60, gt=0)


class CaseInput(_Model):
    case_id: uuid.UUID
    upload_id: uuid.UUID
    time_scale: TimeScaleInput = TimeScaleInput()


# --- signals / queries ------------------------------------------------------------------------


class AbandonRequest(_Model):
    reason: str = Field(default="user_abandoned", max_length=200)


class DraftDecision(_Model):
    """Payload of `approve_draft` / `reject_draft`. Only the ID: the user's edits, name and
    reason are stored on the draft row by the API, so they never enter workflow history."""

    draft_id: uuid.UUID


class MarkSent(_Model):
    """Payload of `mark_sent`: the user sent the email themselves (ManualChannel only)."""

    channel: ChannelKind = ChannelKind.MANUAL
    sent_at: datetime


class CaseState(_Model):
    status: CaseStatus
    needs_confirmation: list[str] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    last_error: str | None = None
    draft_id: uuid.UUID | None = None  # the draft currently waiting for approval
    approved_draft_id: uuid.UUID | None = None
    drafts_used: int = 0
    can_redraft: bool = False  # waiting for a `redraft` signal after a failed draft
    email_id: uuid.UUID | None = None  # the outbound email for the approved draft
    awaiting_manual_send: bool = False  # waiting for `mark_sent`
    can_retry_send: bool = False  # the send was blocked; waiting for `retry_send`


# --- activities -------------------------------------------------------------------------------


class RunIntakeInput(_Model):
    case_id: uuid.UUID
    upload_id: uuid.UUID
    now: datetime


class IntakeSummary(_Model):
    status: IntakeStatus
    needs_confirmation: list[str] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)


class ConfirmInput(_Model):
    case_id: uuid.UUID
    confirmation: Confirmation
    now: datetime


class ConfirmOutcome(_Model):
    needs_confirmation: list[str] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.needs_confirmation and not self.missing_details


class AssessInput(_Model):
    case_id: uuid.UUID
    as_of: date


class AssessSummary(_Model):
    covered: bool
    total_owed_inr: Decimal


class DraftInput(_Model):
    case_id: uuid.UUID
    seq: int = Field(ge=1)  # idempotency key: one draft row per (case, seq)
    letter_date: date


class DraftSummary(_Model):
    draft_id: uuid.UUID
    status: DraftStatus
    rules_verified: bool


class DraftDecisionInput(_Model):
    case_id: uuid.UUID
    draft_id: uuid.UUID
    now: datetime


class PrepareSendInput(_Model):
    case_id: uuid.UUID
    draft_id: uuid.UUID


class SendSummary(_Model):
    email_id: uuid.UUID
    channel: ChannelKind
    status: OutboundStatus
    sandbox: bool
    blocked_reasons: list[str] = Field(default_factory=list)


class RecordSentInput(_Model):
    case_id: uuid.UUID
    email_id: uuid.UUID
    mark: MarkSent
    now: datetime


class MarkAbandonedInput(_Model):
    case_id: uuid.UUID
    reason: str
    actor: Actor


class CaseOutcome(_Model):
    status: CaseStatus
    reason: str | None = None
