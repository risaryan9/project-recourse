"""Evidence + user answers → rule engine (pure, offline). Synthetic data only."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from recourse.cases.assessment import AssessmentError, CaseDetails, assess, missing_details
from recourse.cases.review import Confirmation, InvalidAnswer, review
from recourse.evidence.models import IST, Evidence, Origin
from recourse.rules.models import BankType, Fault

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)
TODAY = date(2026, 11, 17)


def evidence(**facts: Any) -> Evidence:
    base: dict[str, Any] = {
        "case_type": "failed_transaction",
        "scenario": "BENEFICIARY_NOT_CREDITED",
        "channel": "UPI",
        "account_debited": True,
        "amount_inr": "2450",
        "txn_timestamp": "2026-11-14T18:05:00",
        "utr": "631845207719",
        "payer_bank": "Example Bank",
    }
    base.update(facts)
    data: dict[str, Any] = {"source_kind": "SMS", "extracted_by": "test"}
    for name, value in base.items():
        if value is not None:
            data[name] = {"value": value, "confidence": 0.95}
    return Evidence.model_validate(data)


def test_failed_transaction_assessment() -> None:
    ent = assess(evidence(), CaseDetails(), as_of=TODAY)
    assert ent.case_type == "failed_transaction"
    assert ent.covered
    assert ent.total_owed_inr == Decimal("2650.00")  # refund + 2 days x 100 beyond T+1


def test_reversal_date_is_used() -> None:
    ev = evidence(reversal_timestamp="2026-11-15T09:00:00")
    ent = assess(ev, CaseDetails(), as_of=TODAY)
    assert ent.total_owed_inr == Decimal("0")  # reversed within T+1: nothing owed


def test_fraud_needs_bank_type() -> None:
    ev = evidence(case_type="fraud", scenario=None)
    assert missing_details(ev, CaseDetails()) == ["bank_type"]
    assert missing_details(ev, CaseDetails(bank_type=BankType.COMMERCIAL_BANK)) == []
    with pytest.raises(AssessmentError, match="bank_type"):
        assess(ev, CaseDetails(), as_of=TODAY)


def test_fraud_assessment_uses_details() -> None:
    ev = evidence(case_type="fraud", scenario=None)
    details = CaseDetails(
        bank_type=BankType.COMMERCIAL_BANK,
        fault=Fault.THIRD_PARTY,
        reported_to_bank_date=date(2026, 11, 15),
    )
    ent = assess(ev, details, as_of=TODAY)
    assert ent.case_type == "fraud"
    assert ent.regime_id == "FRAUD-A-2025"


def test_failed_transaction_never_needs_details() -> None:
    assert missing_details(evidence(), CaseDetails()) == []


def test_missing_required_fact_is_an_assessment_error() -> None:
    with pytest.raises(AssessmentError, match="scenario"):
        assess(evidence(scenario=None), CaseDetails(), as_of=TODAY)


def test_inconsistent_dates_are_an_assessment_error() -> None:
    ev = evidence(reversal_timestamp="2026-11-20T09:00:00")  # after as_of
    with pytest.raises(AssessmentError):
        assess(ev, CaseDetails(), as_of=TODAY)


def test_details_merge_keeps_earlier_answers() -> None:
    first = CaseDetails(bank_type=BankType.COMMERCIAL_BANK)
    merged = first.merged(CaseDetails.model_validate({"fault": "CUSTOMER"}))
    assert merged.bank_type is BankType.COMMERCIAL_BANK
    assert merged.fault is Fault.CUSTOMER


# --- review ------------------------------------------------------------------------------------


def test_review_applies_corrections_as_user_values() -> None:
    result = review(
        evidence(utr=None),
        CaseDetails(),
        Confirmation(corrections={"utr": "631845207719"}),
        now=NOW,
    )
    assert result.ready
    assert result.evidence.utr.origin is Origin.USER


def test_review_reports_what_is_still_missing() -> None:
    result = review(
        evidence(case_type="fraud", scenario=None), CaseDetails(), Confirmation(), now=NOW
    )
    assert not result.ready
    assert result.missing_details == ["bank_type"]


def test_review_rejects_bad_values_by_field_name() -> None:
    with pytest.raises(InvalidAnswer) as exc:
        review(
            evidence(),
            CaseDetails(),
            Confirmation(corrections={"amount_inr": "lots", "card_last4": "12"}),
            now=NOW,
        )
    assert exc.value.fields == ["amount_inr", "card_last4"]  # all bad fields, in one answer
    assert "lots" not in str(exc.value)


def test_confirmation_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        Confirmation.model_validate({"corrections": {"password": "x"}})
