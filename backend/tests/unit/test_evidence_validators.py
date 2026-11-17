from datetime import datetime
from typing import Any

import pytest

from recourse.evidence.models import IST, Evidence
from recourse.evidence.validators import (
    EvidenceCheck,
    IssueCode,
    Severity,
    check_evidence,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)


def ev(**facts: Any) -> Evidence:
    data: dict[str, Any] = {"source_kind": "SCREENSHOT", "extracted_by": "test"}
    for name, value in facts.items():
        data[name] = value if isinstance(value, dict) else {"value": value, "confidence": 0.99}
    return Evidence.model_validate(data)


def good_upi(**overrides: Any) -> Evidence:
    facts: dict[str, Any] = {
        "case_type": "failed_transaction",
        "scenario": "BENEFICIARY_NOT_CREDITED",
        "channel": "UPI",
        "txn_status": "PENDING",
        "account_debited": True,
        "amount_inr": "2500",
        "txn_timestamp": "2026-11-10T10:15:00",
        "utr": "412345678901",
        "payer_bank": "Example Bank",
    }
    facts.update(overrides)
    return ev(**{k: v for k, v in facts.items() if v is not None})


def check(e: Evidence, **kw: Any) -> EvidenceCheck:
    return check_evidence(e, now=NOW, **kw)


def codes(c: EvidenceCheck, field: str) -> set[IssueCode]:
    return {i.code for i in c.for_field(field)}


def test_clean_upi_case_passes() -> None:
    c = check(good_upi())
    assert c.ok
    assert c.issues == []


def test_empty_evidence_needs_the_core_fields() -> None:
    c = check(ev())
    assert c.needs_user_confirmation == [
        "case_type",
        "channel",
        "amount_inr",
        "txn_timestamp",
        "payer_bank",
    ]


@pytest.mark.parametrize("field", ["scenario", "account_debited", "utr", "amount_inr"])
def test_missing_required_field_blocks(field: str) -> None:
    c = check(good_upi(**{field: None}))
    assert c.needs_user_confirmation == [field]
    assert codes(c, field) == {IssueCode.MISSING}


def test_fraud_case_does_not_need_scenario_or_debit_flag() -> None:
    c = check(
        good_upi(
            case_type="fraud",
            scenario=None,
            account_debited=None,
            alert_received_at="2026-11-10T10:16:00",
        )
    )
    assert c.ok


def test_fraud_case_warns_without_alert_time_and_with_scenario() -> None:
    c = check(good_upi(case_type="fraud"))
    assert c.ok  # warnings only
    assert codes(c, "alert_received_at") == {IssueCode.MISSING}
    assert codes(c, "scenario") == {IssueCode.INCONSISTENT}


@pytest.mark.parametrize("utr", ["41234567890", "4123456789012", "41234567890A"])
def test_bad_utr_blocks_for_upi(utr: str) -> None:
    c = check(good_upi(utr=utr))
    assert c.needs_user_confirmation == ["utr"]
    assert codes(c, "utr") == {IssueCode.INVALID_FORMAT}


def test_bad_reference_only_warns_for_atm() -> None:
    c = check(good_upi(channel="ATM", scenario="CASH_NOT_DISPENSED", utr="1234"))
    assert c.ok
    assert codes(c, "utr") == {IssueCode.INVALID_FORMAT}


def test_utr_optional_for_atm() -> None:
    assert check(good_upi(channel="ATM", scenario="CASH_NOT_DISPENSED", utr=None)).ok


@pytest.mark.parametrize("amount", ["0", "-50"])
def test_amount_must_be_positive(amount: str) -> None:
    c = check(good_upi(amount_inr=amount))
    assert c.needs_user_confirmation == ["amount_inr"]
    assert codes(c, "amount_inr") == {IssueCode.NOT_POSITIVE}


def test_future_transaction_blocks() -> None:
    c = check(good_upi(txn_timestamp="2026-11-17T12:00:01"))
    assert codes(c, "txn_timestamp") == {IssueCode.FUTURE_DATE}
    assert not c.ok


def test_exactly_now_is_fine() -> None:
    assert check(good_upi(txn_timestamp="2026-11-17T12:00:00")).ok


def test_low_confidence_required_field_blocks_optional_warns() -> None:
    e = good_upi(
        amount_inr={"value": "2500", "confidence": 0.6},
        payee_name={"value": "Some Shop", "confidence": 0.5},
    )
    c = check(e)
    assert c.needs_user_confirmation == ["amount_inr"]
    [payee] = c.for_field("payee_name")
    assert (payee.code, payee.severity) == (IssueCode.LOW_CONFIDENCE, Severity.WARNING)


def test_threshold_is_configurable() -> None:
    e = good_upi(amount_inr={"value": "2500", "confidence": 0.6})
    assert check(e, min_confidence=0.5).ok


def test_user_confirmed_value_clears_low_confidence() -> None:
    e = good_upi(amount_inr={"value": "2500", "confidence": 0.3})
    assert not check(e).ok
    assert check(e.with_user_value("amount_inr", "2500")).ok


def test_reversal_before_transaction_blocks() -> None:
    c = check(good_upi(txn_status="REVERSED", reversal_timestamp="2026-11-09T10:00:00"))
    assert codes(c, "reversal_timestamp") == {IssueCode.INCONSISTENT}
    assert not c.ok


def test_reversal_larger_than_amount_blocks() -> None:
    c = check(
        good_upi(
            txn_status="REVERSED",
            reversal_timestamp="2026-11-12T10:00:00",
            reversal_amount_inr="3000",
        )
    )
    assert codes(c, "reversal_amount_inr") == {IssueCode.INCONSISTENT}


def test_valid_reversal_passes() -> None:
    c = check(
        good_upi(
            txn_status="REVERSED",
            reversal_timestamp="2026-11-12T10:00:00",
            reversal_amount_inr="2500",
        )
    )
    assert c.ok and c.issues == []


def test_reversed_without_date_warns() -> None:
    c = check(good_upi(txn_status="REVERSED"))
    assert c.ok
    assert codes(c, "reversal_timestamp") == {IssueCode.MISSING}


def test_reversal_date_with_pending_status_warns() -> None:
    c = check(good_upi(reversal_timestamp="2026-11-12T10:00:00"))
    assert c.ok
    assert codes(c, "txn_status") == {IssueCode.INCONSISTENT}


def test_alert_before_transaction_blocks() -> None:
    c = check(good_upi(case_type="fraud", scenario=None, alert_received_at="2026-11-09T10:00:00"))
    assert codes(c, "alert_received_at") == {IssueCode.INCONSISTENT}
    assert not c.ok


def test_not_debited_failed_txn_warns() -> None:
    c = check(good_upi(account_debited=False))
    assert c.ok
    assert codes(c, "account_debited") == {IssueCode.INCONSISTENT}


def test_now_must_be_aware() -> None:
    with pytest.raises(ValueError):
        check_evidence(good_upi(), now=datetime(2026, 11, 17))


def test_check_is_deterministic() -> None:
    e = ev(utr="123")
    assert check(e) == check(e)
