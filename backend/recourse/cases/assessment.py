"""From confirmed `Evidence` (+ the user's extra answers) to the rule engine's `Entitlement`.

Pure and deterministic: no LLM, no clock (`as_of` is passed in), no I/O. Used by the assess
activity, and by the API to tell the user what is still missing before a case can be assessed.

Some inputs are not visible in any screenshot or SMS, so the user answers them directly
(`CaseDetails`). Fraud cases need the bank type (it selects the rulebook regime); the reporting
dates and fault are optional (missing reporting dates put the case in urgent mode).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from recourse.evidence.models import CaseType, Evidence
from recourse.rules.engine import assess_failed_transaction, assess_fraud
from recourse.rules.models import (
    AccountType,
    BankType,
    Entitlement,
    FailedTxnFacts,
    Fault,
    FraudFacts,
)


class CaseDetails(BaseModel):
    """Answers the user gives directly (not extracted from evidence)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Fraud cases
    bank_type: BankType | None = None
    account_type: AccountType | None = None
    fault: Fault = Fault.UNKNOWN
    reported_to_bank_date: date | None = None
    reported_to_ncrp_date: date | None = None  # cybercrime.gov.in or helpline 1930

    def merged(self, update: CaseDetails | None) -> CaseDetails:
        """Fields the user set in `update` replace the current ones."""
        if update is None:
            return self
        return self.model_copy(update=update.model_dump(exclude_unset=True))


class AssessmentError(ValueError):
    """The confirmed facts cannot be turned into rule-engine input (e.g. inconsistent dates)."""


def missing_details(evidence: Evidence, details: CaseDetails) -> list[str]:
    """`CaseDetails` fields the user must still answer before the case can be assessed."""
    if evidence.case_type.value is CaseType.FRAUD and details.bank_type is None:
        return ["bank_type"]
    return []


def assess(evidence: Evidence, details: CaseDetails, *, as_of: date) -> Entitlement:
    """Run the rule engine. Raises `AssessmentError` if required facts are missing or invalid."""
    case_type = evidence.case_type.value
    try:
        if case_type is CaseType.FAILED_TRANSACTION:
            return assess_failed_transaction(_failed_facts(evidence, as_of))
        if case_type is CaseType.FRAUD:
            return assess_fraud(_fraud_facts(evidence, details, as_of))
    except ValueError as e:  # Pydantic ValidationError is a ValueError
        raise AssessmentError(str(e)) from e
    raise AssessmentError("case_type is not set")


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise AssessmentError(f"{name} is required")
    return value


def _failed_facts(ev: Evidence, as_of: date) -> FailedTxnFacts:
    domestic = ev.is_domestic.value
    return FailedTxnFacts(
        channel=_required(ev.channel.value, "channel"),
        scenario=_required(ev.scenario.value, "scenario"),
        amount_inr=_required(ev.amount_inr.value, "amount_inr"),
        txn_date=_required(ev.txn_date, "txn_timestamp"),
        reversal_date=ev.reversal_date,
        domestic=True if domestic is None else domestic,
        as_of=as_of,
    )


def _fraud_facts(ev: Evidence, details: CaseDetails, as_of: date) -> FraudFacts:
    domestic = ev.is_domestic.value
    return FraudFacts(
        amount_inr=_required(ev.amount_inr.value, "amount_inr"),
        txn_date=_required(ev.txn_date, "txn_timestamp"),
        bank_type=_required(details.bank_type, "bank_type"),
        account_type=details.account_type,
        fault=details.fault,
        alert_received_date=ev.alert_received_date,
        reported_to_bank_date=details.reported_to_bank_date,
        reported_to_ncrp_date=details.reported_to_ncrp_date,
        cross_border=False if domestic is None else not domestic,
        as_of=as_of,
    )
