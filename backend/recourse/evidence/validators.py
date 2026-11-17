"""Deterministic business checks on an `Evidence` object (the "soft" layer).

`check_evidence()` never raises. It returns an `EvidenceCheck` listing every issue:

* **ERROR**: the field must be confirmed or corrected by the user before the rule engine runs.
  The fields with errors are `EvidenceCheck.needs_user_confirmation`.
* **WARNING**: shown to the user, but does not block.

No LLM, no clock: "now" is passed in explicitly, so the same input always gives the same result.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from recourse.evidence.models import CaseType, Evidence, Extracted, Origin, TxnStatus
from recourse.rules.models import Channel

DEFAULT_MIN_CONFIDENCE = 0.85

# UPI UTRs and IMPS RRNs are 12 digits (CLAUDE.md §3, IntakeGraph validators).
_UTR_RE = re.compile(r"^\d{12}$")
_CHANNELS_NEEDING_UTR = frozenset({Channel.UPI, Channel.IMPS})

# Needed for every case: without these the rule engine cannot run.
_ALWAYS_REQUIRED = ("case_type", "channel", "amount_inr", "txn_timestamp", "payer_bank")
# Needed additionally for a failed-transaction claim.
_FAILED_TXN_REQUIRED = ("scenario", "account_debited")


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class IssueCode(StrEnum):
    MISSING = "MISSING"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INVALID_FORMAT = "INVALID_FORMAT"
    NOT_POSITIVE = "NOT_POSITIVE"
    FUTURE_DATE = "FUTURE_DATE"
    INCONSISTENT = "INCONSISTENT"


class FieldIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    code: IssueCode
    severity: Severity
    message: str


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issues: list[FieldIssue]

    @property
    def needs_user_confirmation(self) -> list[str]:
        """Fields the user must confirm or fix, in a stable order, without duplicates."""
        seen: dict[str, None] = {}
        for issue in self.issues:
            if issue.severity is Severity.ERROR:
                seen.setdefault(issue.field, None)
        return list(seen)

    @property
    def ok(self) -> bool:
        return not self.needs_user_confirmation

    def for_field(self, name: str) -> list[FieldIssue]:
        return [i for i in self.issues if i.field == name]


def check_evidence(
    ev: Evidence, *, now: datetime, min_confidence: float = DEFAULT_MIN_CONFIDENCE
) -> EvidenceCheck:
    """Run every deterministic check. `now` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")

    issues: list[FieldIssue] = []

    def add(field: str, code: IssueCode, severity: Severity, message: str) -> None:
        issues.append(FieldIssue(field=field, code=code, severity=severity, message=message))

    required = _required_fields(ev)

    # 1. Missing and low-confidence fields.
    for name in Evidence.fact_names():
        fact: Extracted[Any] = ev.fact(name)
        is_required = name in required
        if not fact.present:
            if is_required:
                add(name, IssueCode.MISSING, Severity.ERROR, "Not found in the evidence.")
            continue
        if fact.origin is Origin.EXTRACTED and fact.confidence < min_confidence:
            add(
                name,
                IssueCode.LOW_CONFIDENCE,
                Severity.ERROR if is_required else Severity.WARNING,
                f"Read with low confidence ({fact.confidence:.0%}). Please check it.",
            )

    # 2. Format and range.
    amount = ev.amount_inr.value
    if amount is not None and amount <= 0:
        add("amount_inr", IssueCode.NOT_POSITIVE, Severity.ERROR, "Amount must be more than ₹0.")

    utr = ev.utr.value
    channel = ev.channel.value
    if utr is not None and not _UTR_RE.fullmatch(utr):
        strict = channel in _CHANNELS_NEEDING_UTR
        add(
            "utr",
            IssueCode.INVALID_FORMAT,
            Severity.ERROR if strict else Severity.WARNING,
            f"A UTR/RRN is 12 digits; this has {len(utr)} characters.",
        )

    for name in ("txn_timestamp", "reversal_timestamp", "alert_received_at"):
        ts = ev.fact(name).value
        if ts is not None and ts > now:
            add(name, IssueCode.FUTURE_DATE, Severity.ERROR, "This date is in the future.")

    reversal_amount = ev.reversal_amount_inr.value
    if reversal_amount is not None and reversal_amount <= 0:
        add(
            "reversal_amount_inr",
            IssueCode.NOT_POSITIVE,
            Severity.ERROR,
            "Reversal amount must be more than ₹0.",
        )

    # 3. Consistency between fields.
    txn_ts = ev.txn_timestamp.value
    rev_ts = ev.reversal_timestamp.value
    if txn_ts is not None and rev_ts is not None and rev_ts < txn_ts:
        add(
            "reversal_timestamp",
            IssueCode.INCONSISTENT,
            Severity.ERROR,
            "The reversal is dated before the transaction.",
        )
    alert_ts = ev.alert_received_at.value
    if txn_ts is not None and alert_ts is not None and alert_ts.date() < txn_ts.date():
        add(
            "alert_received_at",
            IssueCode.INCONSISTENT,
            Severity.ERROR,
            "The bank's alert is dated before the transaction.",
        )
    if amount is not None and reversal_amount is not None and reversal_amount > amount:
        add(
            "reversal_amount_inr",
            IssueCode.INCONSISTENT,
            Severity.ERROR,
            "The reversal is larger than the transaction amount.",
        )
    if ev.txn_status.value is TxnStatus.REVERSED and rev_ts is None:
        add(
            "reversal_timestamp",
            IssueCode.MISSING,
            Severity.WARNING,
            "Marked as reversed, but the reversal date is unknown. It affects compensation.",
        )
    if rev_ts is not None and ev.txn_status.value not in (None, TxnStatus.REVERSED):
        add(
            "txn_status",
            IssueCode.INCONSISTENT,
            Severity.WARNING,
            "A reversal date is present, but the status is not 'reversed'.",
        )
    if ev.case_type.value is CaseType.FAILED_TRANSACTION and ev.account_debited.value is False:
        add(
            "account_debited",
            IssueCode.INCONSISTENT,
            Severity.WARNING,
            "The account was not debited, so there may be no money to recover.",
        )
    if ev.case_type.value is CaseType.FRAUD and ev.scenario.present:
        add(
            "scenario",
            IssueCode.INCONSISTENT,
            Severity.WARNING,
            "A failed-transaction scenario was set on a fraud case; it will be ignored.",
        )
    if ev.case_type.value is CaseType.FRAUD and alert_ts is None:
        add(
            "alert_received_at",
            IssueCode.MISSING,
            Severity.WARNING,
            "When did the bank's alert arrive? Without it, the transaction date is assumed.",
        )

    return EvidenceCheck(issues=issues)


def _required_fields(ev: Evidence) -> set[str]:
    required = set(_ALWAYS_REQUIRED)
    if ev.case_type.value is CaseType.FAILED_TRANSACTION:
        required.update(_FAILED_TXN_REQUIRED)
    if ev.channel.value in _CHANNELS_NEEDING_UTR:
        required.add("utr")
    return required
