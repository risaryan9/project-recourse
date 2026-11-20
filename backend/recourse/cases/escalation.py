"""From a case to the rule engine's RBI Ombudsman escalation assessment (RB-IOS 2026).

Pure and deterministic: no LLM, no clock (`as_of` is passed in), no I/O. Used by the
`assess_escalation` activity, which starts and restarts the bank's reply timer.
"""

from __future__ import annotations

from datetime import date

from recourse.rules.engine import assess_escalation
from recourse.rules.models import (
    BankType,
    Entitlement,
    EscalationAssessment,
    EscalationFacts,
    FraudDetails,
)


def bank_type_for(user_answer: BankType | None, contact_type: BankType | None) -> BankType | None:
    """The user's own answer wins; otherwise the bank contacts file's category; else unknown."""
    return user_answer if user_answer is not None else contact_type


def escalation_for(
    entitlement: Entitlement,
    *,
    bank_type: BankType | None,
    complained_on: date,
    as_of: date,
    last_bank_communication: date | None = None,
    reply_unsatisfactory: bool = False,
) -> EscalationAssessment:
    """Run `assess_escalation` for a case whose complaint reached the bank on `complained_on`.

    The bank's own reply timeline comes from the Entitlement: fraud cases carry it (90 days
    under Regime A, 45/60 under Regime B); failed transactions have none, so the scheme's
    30-day minimum applies.
    """
    details = entitlement.details
    reply_days, reply_cite = None, None
    if isinstance(details, FraudDetails):
        reply_days, reply_cite = details.regulatory_reply_days, details.regulatory_reply_citation
    return assess_escalation(
        EscalationFacts(
            entity_type=bank_type,
            bank_received_complaint_date=complained_on,
            regulatory_reply_days=reply_days,
            regulatory_reply_citation=reply_cite,
            last_bank_communication_date=last_bank_communication,
            reply_unsatisfactory=reply_unsatisfactory,
            as_of=as_of,
        )
    )


def days_until(day: date | None, as_of: date) -> int:
    """Whole days from `as_of` to `day` (0 if it is today, past, or unknown)."""
    if day is None:
        return 0
    return max(0, (day - as_of).days)
