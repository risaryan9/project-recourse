"""Database tables (SQLAlchemy 2, Postgres on Neon). Schema changes go through Alembic only
(`migrations/`). See docs/decisions.md ADR-007 and ADR-008.

Tables:

* `cases`: one row per customer case. `status` mirrors the CaseWorkflow state.
* `evidence`: the checked facts from one submission (an `Evidence` object as JSON) plus the
  validator result. Raw uploads (images, PDFs, SMS text) are never stored, only the redacted,
  structured facts.
* `uploads` / `upload_files`: a submission waiting for intake. **Temporary**: deleted as soon
  as intake has run. Text is PII-redacted before it is stored; images/PDFs cannot be, which is
  why they are kept only for those seconds.
* `entitlements`: the rule engine's answer for a case (one row per case, replaced on reassessment).
* `drafts`: every letter drafted for a case (`seq` 1, 2, ...), its approval state, and the
  final text the user approved (ADR-011).
* `audit_events`: append-only log of everything that happens to a case. Postgres rejects
  UPDATEs on it (trigger in the first migration). Details are PII-redacted before insert
  (`recourse.db.audit.record_event`).

Deleting a case deletes its evidence and audit events (`ON DELETE CASCADE`), for "delete my data".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from recourse.agents.intake_graph import IntakeResult, IntakeStatus
from recourse.evidence.models import Evidence, SourceKind

# Bump when the stored JSON shape of `Evidence` changes incompatibly (and add a data migration).
EVIDENCE_SCHEMA_VERSION = 1

# Deterministic constraint names, so Alembic migrations are stable across machines.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _str_enum(enum: type[StrEnum], length: int = 40) -> Enum:
    """Store a StrEnum as plain VARCHAR (no Postgres enum type, no CHECK constraint), so adding a
    value later needs no migration. Python validates the values."""
    return Enum(
        enum,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class CaseStatus(StrEnum):
    """CaseWorkflow states (CLAUDE.md §3)."""

    NEW = "NEW"
    EXTRACTED = "EXTRACTED"
    ASSESSED = "ASSESSED"
    DRAFT_READY = "DRAFT_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SENT_TO_BANK = "SENT_TO_BANK"
    AWAITING_BANK = "AWAITING_BANK"
    BANK_REPLIED = "BANK_REPLIED"
    JUDGED = "JUDGED"
    RESOLVED = "RESOLVED"
    ESCALATION_DRAFT_READY = "ESCALATION_DRAFT_READY"
    FILED_WITH_OMBUDSMAN = "FILED_WITH_OMBUDSMAN"
    CLOSED = "CLOSED"
    ABANDONED = "ABANDONED"


class Actor(StrEnum):
    """Who caused an audit event."""

    USER = "USER"  # the customer, through the UI/API
    SYSTEM = "SYSTEM"  # deterministic code: workflow, rule engine, validators
    AGENT = "AGENT"  # an LLM-backed step (intake, drafting, reply judging)
    BANK = "BANK"  # something the bank sent (e.g. a reply arrived)


class _Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Case(_Timestamps, Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Short tag for email subjects, e.g. "RC-7F3K" (recourse.db.case_tag).
    case_tag: Mapped[str] = mapped_column(String(16), unique=True)
    status: Mapped[CaseStatus] = mapped_column(_str_enum(CaseStatus), default=CaseStatus.NEW)
    # The user's direct answers (recourse.cases.assessment.CaseDetails as JSON).
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    evidence: Mapped[list[EvidenceRecord]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EvidenceRecord.created_at",
    )


class EvidenceRecord(_Timestamps, Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[SourceKind] = mapped_column(_str_enum(SourceKind))
    extracted_by: Mapped[str] = mapped_column(String(100))
    intake_status: Mapped[IntakeStatus] = mapped_column(_str_enum(IntakeStatus))
    schema_version: Mapped[int] = mapped_column(Integer, default=EVIDENCE_SCHEMA_VERSION)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)  # Evidence.model_dump(mode="json")
    check_result: Mapped[dict[str, Any]] = mapped_column(JSONB)  # EvidenceCheck as JSON

    case: Mapped[Case] = relationship(back_populates="evidence")

    @classmethod
    def from_intake(cls, case_id: uuid.UUID, result: IntakeResult) -> EvidenceRecord:
        if result.evidence is None or result.check is None:
            raise ValueError(f"intake result {result.status} has no evidence to store")
        return cls(
            case_id=case_id,
            source_kind=result.evidence.source_kind,
            extracted_by=result.evidence.extracted_by,
            intake_status=result.status,
            schema_version=EVIDENCE_SCHEMA_VERSION,
            data=result.evidence.model_dump(mode="json"),
            check_result=result.check.model_dump(mode="json"),
        )

    def to_evidence(self) -> Evidence:
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError(f"evidence {self.id} has schema version {self.schema_version}")
        return Evidence.model_validate(self.data)


class Upload(Base):
    """A submission waiting for intake (temporary; deleted once intake has run)."""

    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str | None] = mapped_column(Text)  # already PII-redacted
    # Kinds of sensitive data removed from `text` (so the user can still be warned).
    redactions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    files: Mapped[list[UploadFile]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="UploadFile.position"
    )


class UploadFile(Base):
    __tablename__ = "upload_files"

    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("uploads.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(String(40))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class EntitlementRecord(_Timestamps, Base):
    """The rule engine's `Entitlement` for a case (money, deadlines, citations)."""

    __tablename__ = "entitlements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), unique=True
    )
    as_of: Mapped[date] = mapped_column(Date)
    covered: Mapped[bool] = mapped_column(Boolean)
    total_owed_inr: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)  # Entitlement.model_dump(mode="json")


class DraftKind(StrEnum):
    BANK_COMPLAINT = "BANK_COMPLAINT"


class DraftRecordStatus(StrEnum):
    PENDING = "PENDING"  # waiting for the user's approval
    APPROVED = "APPROVED"  # the user approved `final_subject` / `final_body`
    REJECTED = "REJECTED"  # the user asked for a new draft
    FAILED = "FAILED"  # DraftGraph failed closed: no letter text


class DraftRecord(_Timestamps, Base):
    """One drafted letter. Text columns are set only for a DRAFTED graph result."""

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("case_id", "seq", name="uq_drafts_case_id_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)  # 1, 2, ... per case (the idempotency key)
    kind: Mapped[DraftKind] = mapped_column(_str_enum(DraftKind))
    status: Mapped[DraftRecordStatus] = mapped_column(_str_enum(DraftRecordStatus, length=16))
    letter_date: Mapped[date] = mapped_column(Date)
    attempts: Mapped[int] = mapped_column(Integer)
    rules_verified: Mapped[bool] = mapped_column(Boolean)
    # As drafted (placeholders already filled by code, except the user's own ones).
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    template: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # the LLM's template
    slots_used: Mapped[list[str]] = mapped_column(JSONB, default=list)
    user_fill: Mapped[list[str]] = mapped_column(JSONB, default=list)
    errors: Mapped[list[str]] = mapped_column(JSONB, default=list)  # verify errors when FAILED
    models_used: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # Written by the API from the user's input; acted on by the workflow's activities.
    final_subject: Mapped[str | None] = mapped_column(Text)
    final_body: Mapped[str | None] = mapped_column(Text)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_case_id_occurred_at", "case_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor: Mapped[Actor] = mapped_column(_str_enum(Actor, length=16))
    # Dotted name, e.g. "case.created", "intake.completed", "gmail.thread_get".
    event_type: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
