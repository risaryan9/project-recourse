"""Placeholders ("slots") for drafts: every number, date, reference and clause in a letter.

The LLM writes the letter with `{{slot.name}}` placeholders and never types a value itself.
This module builds the slot table for a case from two trusted sources:

* the rule engine's `Entitlement` (money, deadlines, durations, citations), and
* the user-confirmed `Evidence` (amount, date, UTR, bank and payee names),

then renders a template by replacing each placeholder with its formatted value.

Each `Slot` has a `description` (shown to the LLM) and a `value` (only ever inserted by
`render`). Descriptions may quote rule text (which contains numbers), but they never contain
anything from the user's evidence: payee names, bank names and references never reach the
drafting model, which removes a prompt-injection path. Whatever the model reads, `verify`
rejects any number it types itself.

Formatting is Indian: `₹1,25,000.00`, `19 November 2026`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from recourse.evidence.models import IST, Evidence
from recourse.rules.models import (
    Channel,
    Citation,
    Entitlement,
    EscalationAssessment,
    FailedScenario,
    FailedTxnDetails,
    FraudDetails,
    LiabilityKind,
)

# `{{name}}`, where name is dotted lowercase words/digits, e.g. {{money.refund}}, {{cite.c2}}.
SLOT_RE = re.compile(r"\{\{\s*([a-z0-9_.]+)\s*\}\}")
# Anything that looks like an attempt at a placeholder (to catch malformed ones).
SLOT_LIKE_RE = re.compile(r"\{\{|\}\}|\{[a-z_]+\.[a-z0-9_.]+\}")

CUSTOMER_NAME_PLACEHOLDER = "[Your full name]"

CHANNEL_NAMES: dict[Channel, str] = {
    Channel.ATM: "ATM",
    Channel.CARD_TO_CARD: "card-to-card transfer",
    Channel.CARD_POS: "card payment at a point of sale",
    Channel.CARD_NOT_PRESENT: "online card payment",
    Channel.IMPS: "IMPS transfer",
    Channel.UPI: "UPI payment",
    Channel.AEPS: "AePS transaction",
    Channel.APBS: "APBS credit",
    Channel.NACH: "NACH debit",
    Channel.PPI_ON_US: "wallet (PPI) payment",
}

SCENARIO_NAMES: dict[FailedScenario, str] = {
    FailedScenario.CASH_NOT_DISPENSED: "my account was debited but the cash was not dispensed",
    FailedScenario.BENEFICIARY_NOT_CREDITED: (
        "my account was debited but the beneficiary was not credited"
    ),
    FailedScenario.MERCHANT_NOT_CONFIRMED: (
        "my account was debited but the merchant did not confirm the payment"
    ),
    FailedScenario.MANDATE_REVOKED_DEBIT: "my account was debited despite a revoked mandate",
    FailedScenario.CUSTOMER_ERROR: "the payment went to the wrong recipient",
}


class BankResponse(StrEnum):
    """How the bank answered the complaint, for an Ombudsman complaint (decided by code)."""

    NO_REPLY = "NO_REPLY"  # no substantive reply within the time allowed
    PARTIAL = "PARTIAL"  # paid only part of what is owed
    REJECTED_NO_REASONS = "REJECTED_NO_REASONS"
    REJECTED_WITH_REASONS = "REJECTED_WITH_REASONS"  # the customer does not accept the reasons
    UNSATISFACTORY = "UNSATISFACTORY"  # any other reply the customer found unsatisfactory


BANK_RESPONSE_TEXT: dict[BankResponse, str] = {
    BankResponse.NO_REPLY: (
        "the bank did not resolve my complaint or send a substantive reply within the time allowed"
    ),
    BankResponse.PARTIAL: "the bank paid only part of the amount I am owed",
    BankResponse.REJECTED_NO_REASONS: "the bank rejected my complaint without giving reasons",
    BankResponse.REJECTED_WITH_REASONS: (
        "the bank rejected my complaint, and I do not accept the reasons it gave"
    ),
    BankResponse.UNSATISFACTORY: "the bank's reply did not resolve my complaint",
}


class EscalationContext(BaseModel):
    """What an Ombudsman complaint needs beyond the Entitlement. Stored with the draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complained_on: date
    complaint_ref: str | None = None  # the bank's reference, from a genuine reply
    bank_response: BankResponse
    last_reply_on: date | None = None
    assessment: EscalationAssessment  # rule engine: RB-IOS dates and citations


@dataclass(frozen=True)
class Slot:
    name: str
    description: str  # for the LLM; never contains evidence text
    value: str  # inserted only by `render`
    required: bool = False  # verify fails if the letter does not use it


@dataclass(frozen=True)
class SlotTable:
    slots: dict[str, Slot]
    citation_slots: dict[str, Citation]  # "cite.c1" -> the citation it renders

    @property
    def required(self) -> list[str]:
        return [s.name for s in self.slots.values() if s.required]

    def catalogue(self) -> str:
        """Slot names and descriptions, one per line, for the drafting prompt (no values)."""
        lines = []
        for s in self.slots.values():
            marker = " (REQUIRED)" if s.required else ""
            lines.append(f"{{{{{s.name}}}}}{marker}: {s.description}")
        return "\n".join(lines)


# =================================================================================================
# Formatting
# =================================================================================================


def format_inr(amount: Decimal) -> str:
    """Indian digit grouping: Decimal('125000') -> '₹1,25,000.00'."""
    q = amount.quantize(Decimal("0.01"))
    sign = "-" if q < 0 else ""
    rupees, paise = f"{abs(q):.2f}".split(".")
    if len(rupees) > 3:
        head, tail = rupees[:-3], rupees[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        rupees = ",".join([*groups, tail])
    return f"{sign}₹{rupees}.{paise}"


def format_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def format_datetime(dt: datetime) -> str:
    local = dt.astimezone(IST)
    return f"{format_date(local.date())}, {local.strftime('%H:%M')} IST"


def format_days(n: int, kind: str = "") -> str:
    unit = "day" if n == 1 else "days"
    return f"{n} {kind + ' ' if kind else ''}{unit}"


def format_citation(c: Citation) -> str:
    return f"{c.para} of the {c.doc_title} ({c.doc_ref})"


def format_citation_short(c: Citation) -> str:
    """Used after the document has been named once: 'para 75 of RBI/DOR/2025-26/170'."""
    return f"{c.para} of {c.doc_ref.split(',')[0].strip()}"


# =================================================================================================
# Building the table
# =================================================================================================


class SlotError(ValueError):
    """The case cannot be drafted (e.g. it is not covered by the rulebook)."""


def build_slots(
    entitlement: Entitlement,
    evidence: Evidence,
    *,
    case_tag: str,
    letter_date: date,
    escalation: EscalationContext | None = None,
) -> SlotTable:
    """All placeholders a letter for this case may use: a bank complaint, or (with
    `escalation`) a complaint to the RBI Ombudsman."""
    if not entitlement.covered:
        raise SlotError(f"case is not covered ({entitlement.not_covered_reason})")
    if evidence.case_type.value is None or evidence.case_type.value.value != entitlement.case_type:
        raise SlotError("evidence and entitlement disagree on the case type")

    slots: dict[str, Slot] = {}

    def add(name: str, description: str, value: str, *, required: bool = False) -> None:
        slots[name] = Slot(name, description, value, required)

    # --- Case and customer -----------------------------------------------------------------------
    add(
        "case.tag",
        "Case reference tag; must appear in the subject line.",
        f"[{case_tag}]",
        required=True,
    )
    add("letter.date", "Today's date, for the top of the letter.", format_date(letter_date))
    add(
        "customer.name",
        "The customer's name, for the sign-off.",
        CUSTOMER_NAME_PLACEHOLDER,
        required=True,
    )

    # --- Transaction facts (user-confirmed evidence) ----------------------------------------------
    ev = evidence
    if ev.amount_inr.value is not None:
        add(
            "txn.amount",
            "Amount of the disputed transaction.",
            format_inr(ev.amount_inr.value),
            required=True,
        )
    if ev.txn_timestamp.value is not None:
        add(
            "txn.date",
            "Date of the transaction.",
            format_date(ev.txn_timestamp.value.astimezone(IST).date()),
            required=True,
        )
        add(
            "txn.datetime",
            "Date and time of the transaction.",
            format_datetime(ev.txn_timestamp.value),
        )
    if ev.channel.value is not None:
        add(
            "txn.channel",
            "Kind of transaction, in words (e.g. 'UPI payment').",
            CHANNEL_NAMES[ev.channel.value],
        )
    # The transaction reference: required, so the bank can find the transaction.
    if ev.utr.value is not None:
        add("txn.utr", "Transaction reference number (UTR / RRN).", ev.utr.value, required=True)
    if ev.other_reference.value is not None:
        add(
            "txn.other_reference",
            "Other reference (app transaction ID or order ID).",
            ev.other_reference.value,
            required=ev.utr.value is None,
        )
    if ev.payer_bank.value is not None:
        add(
            "bank.name",
            "Name of the customer's bank (the recipient of this letter).",
            ev.payer_bank.value,
        )
    if ev.payer_account_last4.value is not None:
        add(
            "account.masked",
            "Customer's account number, masked to the last 4 digits.",
            "XXXX" + ev.payer_account_last4.value,
        )
    if ev.card_last4.value is not None:
        add(
            "card.masked",
            "Customer's card number, masked to the last 4 digits.",
            "XXXX XXXX XXXX " + ev.card_last4.value,
        )
    if ev.payment_app.value is not None:
        add("txn.app", "App the payment was made from.", ev.payment_app.value)
    if ev.payee_name.value is not None:
        add("payee.name", "Name of the payee / merchant.", ev.payee_name.value)
    if ev.payee_vpa.value is not None:
        add("payee.vpa", "Payee's UPI ID.", ev.payee_vpa.value)
    if ev.payee_bank.value is not None:
        add("payee.bank", "Payee's bank.", ev.payee_bank.value)
    if ev.atm_or_terminal_id.value is not None:
        add("txn.terminal", "ATM or terminal ID.", ev.atm_or_terminal_id.value)

    # --- Citations --------------------------------------------------------------------------------
    citation_slots: dict[str, Citation] = {}
    cite_name: dict[tuple[str, str], str] = {}

    def cite_of(c: Citation) -> str:
        """The slot name for a citation, adding a slot the first time it is seen."""
        key = (c.doc_id, c.para)
        if key not in cite_name:
            name = f"cite.c{len(cite_name) + 1}"
            cite_name[key] = name
            citation_slots[name] = c
            quote = " / ".join(c.quotes)
            add(
                name, f'Citation: {c.para} of {c.doc_title}. It says: "{quote}"', format_citation(c)
            )
        return cite_name[key]

    # Some citations live only in `details` (e.g. the fraud resolution time), so `cite_of` also
    # registers citations on demand.
    for c in entitlement.citations:
        cite_of(c)

    def require(name: str) -> None:
        slots[name] = Slot(name, slots[name].description, slots[name].value, True)

    # --- Money (rule engine) ----------------------------------------------------------------------
    for m in entitlement.money:
        accruing = " It is still growing until the bank pays." if m.accruing else ""
        add(
            f"money.{m.key}",
            f"{m.label} (supported by {{{{{cite_of(m.citation)}}}}}).{accruing}",
            format_inr(m.amount_inr),
            required=True,
        )
        require(cite_of(m.citation))
    if len(entitlement.money) > 1:
        add(
            "money.total",
            "Total amount owed (sum of the amounts above).",
            format_inr(entitlement.total_owed_inr),
            required=True,
        )

    # --- Deadlines (rule engine) ------------------------------------------------------------------
    for d in entitlement.deadlines:
        status = "already passed" if d.passed else "not yet passed"
        approx = " (approximate: working days)" if d.approximate else ""
        add(
            f"deadline.{d.key}",
            f"{d.label}{approx}; {status} (supported by {{{{{cite_of(d.citation)}}}}}).",
            format_date(d.due),
            required=entitlement.case_type == "failed_transaction",
        )
        if entitlement.case_type == "failed_transaction":
            require(cite_of(d.citation))

    # --- Case-type details ------------------------------------------------------------------------
    det = entitlement.details
    if isinstance(det, FailedTxnDetails):
        add(
            "rule.tat",
            "The turnaround time the bank had to fix this (e.g. 'T+1').",
            f"T+{det.tat_days}",
        )
        add("rule.tat_days", "That turnaround time as a number of days.", format_days(det.tat_days))
        if det.compensation_per_day_inr:
            add(
                "rule.compensation_per_day",
                "Compensation per day of delay (renders as an amount followed by 'per day').",
                f"{format_inr(Decimal(det.compensation_per_day_inr))} per day",
            )
        if det.days_delayed:
            add(
                "fact.days_delayed",
                "Number of days of delay so far.",
                format_days(det.days_delayed),
            )
        if det.reversal_date is not None:
            add(
                "fact.reversal_date", "Date the money was returned.", format_date(det.reversal_date)
            )
        if ev.scenario.value is not None:
            add(
                "txn.problem",
                "What went wrong, in the customer's words (first person).",
                SCENARIO_NAMES[ev.scenario.value],
            )
    elif isinstance(det, FraudDetails):
        reply_cite = cite_of(det.regulatory_reply_citation)
        require(reply_cite)
        add(
            "rule.bank_resolution_days",
            "Maximum time the bank has to resolve this complaint "
            f"(supported by {{{{{reply_cite}}}}}).",
            format_days(det.regulatory_reply_days),
            required=True,
        )
        for o in det.outcomes:
            if o.kind is LiabilityKind.ZERO:
                require(cite_of(o.citation))
        if det.reporting_delay_days is not None and not det.hypothetical_report_today:
            add(
                "fact.reporting_delay",
                "How long after the bank's alert the customer reported.",
                format_days(det.reporting_delay_days, det.reporting_day_kind.value.lower()),
            )
    # --- Escalation to the RBI Ombudsman (rule engine: RB-IOS 2026) -------------------------------
    if escalation is not None:
        add(
            "bank.complaint_date",
            "Date the customer complained to the bank.",
            format_date(escalation.complained_on),
            required=True,
        )
        if escalation.complaint_ref is not None:
            add(
                "bank.complaint_ref",
                "The bank's reference number for the customer's complaint.",
                escalation.complaint_ref,
                required=True,
            )
        add(
            "bank.response",
            "What the bank did about the complaint, as a first-person clause (e.g. used after "
            "'However, ').",
            BANK_RESPONSE_TEXT[escalation.bank_response],
            required=True,
        )
        if escalation.last_reply_on is not None:
            add(
                "bank.reply_date",
                "Date of the bank's last reply.",
                format_date(escalation.last_reply_on),
            )
        for c in escalation.assessment.citations:
            cite_of(c)
        for d in escalation.assessment.deadlines:
            name = cite_of(d.citation)
            add(
                f"deadline.{d.key}",
                f"{d.label} (supported by {{{{{name}}}}}).",
                format_date(d.due),
            )
            if d.key == "ombudsman_eligible_from":
                require(name)  # why the complaint is maintainable now
    return SlotTable(slots=slots, citation_slots=citation_slots)


# =================================================================================================
# Rendering
# =================================================================================================


def used_slots(template: str) -> list[str]:
    return SLOT_RE.findall(template)


def render(template: str, table: SlotTable) -> str:
    """Replace every placeholder with its value. Unknown names raise `KeyError` (verify first).

    A citation names its document in full the first time that document appears, and in short
    form (paragraph + reference number) after that.
    """
    named_docs: set[str] = set()

    def value(m: re.Match[str]) -> str:
        name = m.group(1)
        citation = table.citation_slots.get(name)
        if citation is None:
            return table.slots[name].value
        if citation.doc_id in named_docs:
            return format_citation_short(citation)
        named_docs.add(citation.doc_id)
        return table.slots[name].value

    return SLOT_RE.sub(value, template)


def strip_slots(template: str) -> str:
    """The template with every placeholder removed: the text the LLM typed itself."""
    return SLOT_RE.sub(" ", template)
