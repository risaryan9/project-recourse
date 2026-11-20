"""Draft approval logic shared by the API (for an immediate answer) and the workflow's
activities (which make the decision). Pure functions plus one database helper; no LLM.

The user approves a draft by giving their name (it replaces `[Your full name]`) and, optionally,
their own edited subject and body. The edited text must still pass `check_final`: the case tag
stays in the subject, and every amount and date is still one of the rule engine's (ADR-011).
"""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recourse.agents.draft_slots import (
    CUSTOMER_NAME_PLACEHOLDER,
    EscalationContext,
    SlotTable,
    build_slots,
)
from recourse.agents.draft_verify import MAX_BODY_CHARS, MAX_SUBJECT_CHARS, check_final
from recourse.db.models import Case, DraftKind, DraftRecord, EntitlementRecord, EvidenceRecord
from recourse.rules.models import Entitlement

MAX_DRAFTS_PER_CASE = 5  # every draft is a paid LLM run; the user can still edit the last one
MAX_REASON_CHARS = 500

_NAME_RE = re.compile(r"^[^\d\n\r{}<>\[\]]{2,100}$")


class InvalidEdit(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class ApprovalEdits(BaseModel):
    """What the user sends to approve a draft."""

    model_config = ConfigDict(extra="forbid")

    customer_name: str = Field(min_length=2, max_length=100)
    subject: str | None = Field(default=None, max_length=MAX_SUBJECT_CHARS)
    body: str | None = Field(default=None, max_length=MAX_BODY_CHARS)


class Rejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=MAX_REASON_CHARS)


def final_letter(
    draft_subject: str, draft_body: str, edits: ApprovalEdits, table: SlotTable
) -> tuple[str, str]:
    """The letter the user approves: their edits (if any) with their name filled in.

    Raises `InvalidEdit` with every problem found.
    """
    name = edits.customer_name.strip()
    if not _NAME_RE.match(name):
        raise InvalidEdit(["name must be 2-100 characters, without digits or brackets"])
    subject = (edits.subject if edits.subject is not None else draft_subject).strip()
    body = (edits.body if edits.body is not None else draft_body).strip()
    subject = subject.replace(CUSTOMER_NAME_PLACEHOLDER, name)
    body = body.replace(CUSTOMER_NAME_PLACEHOLDER, name)
    result = check_final(subject, body, table)
    if not result.ok:
        raise InvalidEdit(result.errors)
    return subject, body


def recheck_final(subject: str, body: str, table: SlotTable) -> None:
    """Check text that was already through `final_letter` (the activity's second check)."""
    result = check_final(subject, body, table)
    if not result.ok:
        raise InvalidEdit(result.errors)


async def slot_table_for(s: AsyncSession, case: Case, draft: DraftRecord) -> SlotTable:
    """Rebuild the slot table a draft was made from (same entitlement, evidence and date)."""
    record = await s.scalar(
        select(EvidenceRecord)
        .where(EvidenceRecord.case_id == case.id)
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    )
    ent_row = await s.scalar(select(EntitlementRecord).where(EntitlementRecord.case_id == case.id))
    if record is None or ent_row is None:
        raise InvalidEdit(["the case has no assessment to check the letter against"])
    entitlement = Entitlement.model_validate(ent_row.data)
    escalation = None
    if draft.kind is DraftKind.OMBUDSMAN_COMPLAINT:
        if draft.context is None:
            raise InvalidEdit(["the draft has no escalation context to check against"])
        escalation = EscalationContext.model_validate(draft.context)
    return build_slots(
        entitlement,
        record.to_evidence(),
        case_tag=case.case_tag,
        letter_date=draft.letter_date,
        escalation=escalation,
    )


async def latest_draft(s: AsyncSession, case_id: uuid.UUID) -> DraftRecord | None:
    draft: DraftRecord | None = await s.scalar(
        select(DraftRecord)
        .where(DraftRecord.case_id == case_id)
        .order_by(DraftRecord.seq.desc())
        .limit(1)
    )
    return draft
