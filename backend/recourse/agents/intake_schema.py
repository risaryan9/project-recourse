"""What the intake LLM calls fill in, and how their answers become an `Evidence` object.

The LLM never fills `Evidence` directly. It fills these simpler forms (plain strings and enums,
every field required and nullable, so both providers' strict structured output accepts them),
and `to_evidence()` converts the answer:

* each fact keeps its confidence and the verbatim text it was read from;
* any value that fails `Evidence`'s hard checks (an unparseable amount or date, a bad card
  number) is **dropped to "missing"** with a note, so the validators ask the user for it instead
  of us guessing;
* `Evidence` itself redacts PII and masks card/account numbers on the way in.

Field descriptions below are sent to the model as part of the JSON schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from recourse.evidence.models import CaseType, Evidence, SourceKind, TxnStatus
from recourse.rules.models import Channel, FailedScenario
from recourse.security.pii import RedactionKind, redact_text

MAX_NOTES = 5
MAX_NOTE_CHARS = 300
MAX_SOURCE_TEXT = 500
_MAX_EVIDENCE_NOTES = 10  # Evidence.notes max_length

# =================================================================================================
# Step 1: classification
# =================================================================================================


class InputKind(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    SMS = "SMS"
    STATEMENT_PDF = "STATEMENT_PDF"
    EMAIL = "EMAIL"
    USER_DESCRIPTION = "USER_DESCRIPTION"
    UNRELATED = "UNRELATED"  # not evidence of a payment problem at all


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: InputKind = Field(
        description="What the input mainly is. UNRELATED if it is not about a payment, bank "
        "transaction or money lost (e.g. a random photo or unrelated text)."
    )
    reason: str = Field(description="One short sentence explaining the choice. No personal data.")

    @property
    def source_kind(self) -> SourceKind | None:
        return None if self.kind is InputKind.UNRELATED else SourceKind(self.kind.value)


# =================================================================================================
# Step 2: extraction
# =================================================================================================

_CONF = (
    "How sure you are that value is correct, 0.0-1.0. Use >= 0.9 only when the value is printed "
    "clearly in the evidence; lower it if it is blurry, partly hidden, or inferred."
)
_SRC = (
    "The exact words from the evidence the value was read from (copy them verbatim, max 200 "
    "characters), or null if value is null or was inferred from the user's description."
)


class Fact[T](BaseModel):
    """One fact as the model reports it. What `value` means is in the field's description."""

    model_config = ConfigDict(extra="forbid")

    value: T | None = Field(description="The value, or null if the evidence does not show it.")
    confidence: float = Field(description=_CONF)
    source_text: str | None = Field(description=_SRC)


_DATETIME = (
    " Give it as ISO 8601 without timezone, e.g. 2026-11-14T18:05:00 (or 2026-11-14 if no time "
    "is shown), in the local time printed on the document. Indian documents write dates "
    "day-first (14-11-26 is 14 November 2026)."
)
_AMOUNT = " Rupees exactly as written, e.g. '₹2,450.00' or 'Rs 500'."
_LAST4 = " Only the last 4 digits, never the full number."


class ExtractionOutput(BaseModel):
    """Facts about ONE transaction. Mirrors the facts of `Evidence`."""

    model_config = ConfigDict(extra="forbid")

    case_type: Fact[CaseType] = Field(
        description="What went wrong (your reading). failed_transaction: money was debited but "
        "the payment did not complete or reach the payee (technical failure). fraud: the "
        "customer did not make or authorise the payment, or was tricked or scammed into it."
    )
    scenario: Fact[FailedScenario] = Field(
        description="Only for failed_transaction. CASH_NOT_DISPENSED: ATM debited but no cash. "
        "BENEFICIARY_NOT_CREDITED: transfer to a person/account debited but not received. "
        "MERCHANT_NOT_CONFIRMED: payment to a shop/merchant/website debited but not confirmed. "
        "MANDATE_REVOKED_DEBIT: autopay/NACH debit after the customer cancelled the mandate. "
        "CUSTOMER_ERROR: the customer paid the wrong person, ID or amount themselves."
    )
    channel: Fact[Channel] = Field(
        description="Payment channel. ATM (incl. micro-ATM), CARD_TO_CARD, CARD_POS (card used "
        "at a shop terminal), CARD_NOT_PRESENT (card used online), IMPS, UPI, AEPS "
        "(Aadhaar-based payment), APBS, NACH (autopay/ECS mandate), PPI_ON_US (wallet-to-wallet "
        "within one wallet provider)."
    )
    txn_status: Fact[TxnStatus] = Field(
        description="Status as the document shows it: SUCCESS, FAILED (incl. declined), PENDING "
        "(incl. processing/deemed/awaiting), REVERSED (money already returned)."
    )
    account_debited: Fact[bool] = Field(
        description="true if the customer's account or card was debited."
    )
    amount_inr: Fact[str] = Field(description="Transaction amount." + _AMOUNT)
    txn_timestamp: Fact[str] = Field(description="When the transaction happened." + _DATETIME)
    is_domestic: Fact[bool] = Field(
        description="true if the transaction is within India (INR, Indian payment rails)."
    )
    utr: Fact[str] = Field(
        description="UPI UTR / UPI Ref No / IMPS or card RRN (usually 12 digits)."
    )
    other_reference: Fact[str] = Field(
        description="Any other reference: app transaction ID, order ID, complaint number."
    )
    payer_bank: Fact[str] = Field(description="The customer's bank name as shown.")
    payer_account_last4: Fact[str] = Field(description="Customer's account number." + _LAST4)
    card_last4: Fact[str] = Field(description="Card used." + _LAST4)
    payment_app: Fact[str] = Field(description="Payment app used, e.g. a UPI app name.")
    payee_name: Fact[str] = Field(description="Who was paid (person or merchant).")
    payee_vpa: Fact[str] = Field(description="Payee UPI ID, e.g. name@bank.")
    payee_bank: Fact[str] = Field(description="Payee's bank.")
    atm_or_terminal_id: Fact[str] = Field(description="ATM ID or card terminal ID.")
    reversal_amount_inr: Fact[str] = Field(
        description="Amount already credited back to the customer, if any." + _AMOUNT
    )
    reversal_timestamp: Fact[str] = Field(description="When the money came back." + _DATETIME)
    alert_received_at: Fact[str] = Field(
        description="When the customer received the bank's SMS/email alert for this "
        "transaction." + _DATETIME
    )
    notes: list[str] = Field(
        description="Up to 5 short remarks for the reviewer, e.g. 'amount partly hidden', "
        "'statement shows several transactions; picked the failed one'. No personal data."
    )


# =================================================================================================
# Conversion
# =================================================================================================


def to_evidence(
    extraction: ExtractionOutput,
    *,
    source_kind: SourceKind,
    extracted_by: str,
    input_redactions: Iterable[RedactionKind] = (),
) -> Evidence:
    """Build `Evidence`, dropping any fact that fails its hard checks (it becomes "missing").

    `input_redactions` are the kinds of sensitive data removed from the user's input before it
    reached the model; they are recorded so the UI can warn the user.
    """
    notes = [_short_note(n) for n in extraction.notes[:MAX_NOTES] if n.strip()]
    facts: dict[str, dict[str, Any]] = {}
    for name in Evidence.fact_names():
        raw = getattr(extraction, name)
        if raw.value is None:
            facts[name] = {}
            continue
        source = raw.source_text[:MAX_SOURCE_TEXT] if raw.source_text else None
        facts[name] = {
            "value": raw.value,
            "confidence": min(max(raw.confidence, 0.0), 1.0),
            "source_text": source,
        }

    dropped: list[str] = []
    for _ in range(len(facts) + 1):
        try:
            return Evidence.model_validate(
                {
                    "source_kind": source_kind,
                    "extracted_by": extracted_by,
                    "notes": _with_drop_notes(notes, dropped),
                    "redactions": sorted(set(input_redactions)),
                    **facts,
                }
            )
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
            bad_facts = bad & facts.keys()
            if not bad_facts:
                raise
            for name in sorted(bad_facts):
                facts[name] = {}
                dropped.append(name)
    raise AssertionError("unreachable")  # pragma: no cover


def _with_drop_notes(notes: list[str], dropped: list[str]) -> list[str]:
    """Keep "could not read" notes (they explain a missing field); trim model notes to fit."""
    extra = [f"Could not read {name}; please enter it." for name in dropped]
    return notes[: max(0, _MAX_EVIDENCE_NOTES - len(extra))] + extra[:_MAX_EVIDENCE_NOTES]


def _short_note(note: str) -> str:
    text, _ = redact_text(note.strip())
    return text[:MAX_NOTE_CHARS]
