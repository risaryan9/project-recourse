from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from recourse.evidence.models import (
    IST,
    CaseType,
    Evidence,
    Extracted,
    FactName,
    Origin,
    SourceKind,
)
from recourse.rules.models import Channel, FailedScenario
from recourse.security.pii import RedactionKind


def ev(**facts: Any) -> Evidence:
    """Build Evidence from raw values, as an LLM's JSON output would arrive."""
    data: dict[str, Any] = {"source_kind": "SCREENSHOT", "extracted_by": "test"}
    for name, value in facts.items():
        data[name] = value if isinstance(value, dict) else {"value": value, "confidence": 0.99}
    return Evidence.model_validate(data)


def test_empty_evidence_has_every_fact_missing() -> None:
    e = ev()
    assert all(not e.fact(n).present for n in Evidence.fact_names())
    assert e.redactions == []
    assert e.txn_date is None


def test_fact_name_literal_matches_model() -> None:
    assert list(get_args(FactName)) == Evidence.fact_names()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("₹1,250.00", Decimal("1250.00")),
        ("Rs. 1250", Decimal("1250")),
        ("INR 1,25,000/-", Decimal("125000")),
        ("499.5", Decimal("499.5")),
        (1250, Decimal("1250")),
        (0.1, Decimal("0.1")),  # floats go through str, so no binary noise
    ],
)
def test_money_parsing(raw: Any, expected: Decimal) -> None:
    assert ev(amount_inr=raw).amount_inr.value == expected


@pytest.mark.parametrize("raw", ["five hundred", "12.345", "₹"])
def test_bad_money_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        ev(amount_inr=raw)


def test_naive_timestamp_assumed_ist() -> None:
    e = ev(txn_timestamp="2026-11-10T23:30:00")
    assert e.txn_timestamp.value == datetime(2026, 11, 10, 23, 30, tzinfo=IST)
    assert e.txn_date == date(2026, 11, 10)


def test_txn_date_is_the_ist_date() -> None:
    # 20:00 UTC on 10 Nov is 01:30 IST on 11 Nov.
    e = ev(txn_timestamp=datetime(2026, 11, 10, 20, 0, tzinfo=UTC))
    assert e.txn_date == date(2026, 11, 11)


def test_reversal_and_alert_dates() -> None:
    e = ev(reversal_timestamp="2026-11-12T09:00:00", alert_received_at="2026-11-10T10:01:00")
    assert e.reversal_date == date(2026, 11, 12)
    assert e.alert_received_date == date(2026, 11, 10)


@pytest.mark.parametrize(
    ("raw", "last4"),
    [
        ("XXXX XXXX 1234", "1234"),
        ("XX1234", "1234"),
        ("1234", "1234"),
        ("4111111111111111", "1111"),
    ],
)
def test_last4_fields_keep_only_last4(raw: str, last4: str) -> None:
    e = ev(card_last4=raw, payer_account_last4=raw)
    assert e.card_last4.value == last4
    assert e.payer_account_last4.value == last4


def test_last4_needs_four_digits() -> None:
    with pytest.raises(ValidationError):
        ev(card_last4="XX12")


def test_utr_spaces_removed() -> None:
    assert ev(utr="4123 4567 8901").utr.value == "412345678901"


def test_enums_parse_and_reject() -> None:
    e = ev(channel="UPI", scenario="BENEFICIARY_NOT_CREDITED", case_type="failed_transaction")
    assert e.channel.value is Channel.UPI
    assert e.scenario.value is FailedScenario.BENEFICIARY_NOT_CREDITED
    assert e.case_type.value is CaseType.FAILED_TRANSACTION
    with pytest.raises(ValidationError):
        ev(channel="BITCOIN")


def test_confidence_range() -> None:
    with pytest.raises(ValidationError):
        ev(amount_inr={"value": "100", "confidence": 1.5})


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate({"source_kind": "SMS", "extracted_by": "x", "pan": "ABCDE1234F"})
    with pytest.raises(ValidationError):
        ev(amount_inr={"value": "100", "confidence": 0.9, "guess": True})


def test_evidence_is_immutable() -> None:
    e = ev(amount_inr="100")
    with pytest.raises(ValidationError):
        e.amount_inr = Extracted(value=Decimal("1"))  # type: ignore[misc]


def test_secrets_redacted_from_source_text_and_notes() -> None:
    e = Evidence.model_validate(
        {
            "source_kind": SourceKind.SMS,
            "extracted_by": "test",
            "notes": ["SMS also shows OTP 482913"],
            "amount_inr": {
                "value": "500",
                "confidence": 0.9,
                "source_text": "Rs 500 debited from A/c no. 123456789012, card 4111111111111111",
            },
        }
    )
    dumped = e.model_dump_json()
    for secret in ("482913", "123456789012", "4111111111111111"):
        assert secret not in dumped
    assert e.redactions == [
        RedactionKind.ACCOUNT_NUMBER,
        RedactionKind.CARD_NUMBER,
        RedactionKind.SECRET_CODE,
    ]


def test_redaction_also_applies_to_model_instances() -> None:
    e = Evidence(
        source_kind=SourceKind.SMS,
        extracted_by="test",
        payee_name=Extracted(value="OTP 445566", confidence=0.9),
    )
    assert e.payee_name.value == "OTP [REDACTED]"
    assert e.redactions == [RedactionKind.SECRET_CODE]


def test_with_user_value_sets_user_origin_and_revalidates() -> None:
    e = ev(amount_inr={"value": "1000", "confidence": 0.4, "source_text": "Rs 1000"})
    fixed = e.with_user_value("amount_inr", "₹1,500")
    assert fixed.amount_inr == Extracted(
        value=Decimal("1500"), confidence=1.0, source_text=None, origin=Origin.USER
    )
    assert e.amount_inr.value == Decimal("1000")  # original untouched
    with pytest.raises(ValidationError):
        e.with_user_value("amount_inr", "lots")
    with pytest.raises(KeyError):
        e.with_user_value("source_kind", "SMS")


def test_with_user_value_keeps_earlier_redactions() -> None:
    e = ev(payee_name="OTP 445566")
    assert e.with_user_value("channel", "UPI").redactions == [RedactionKind.SECRET_CODE]


def test_user_origin_must_be_certain() -> None:
    with pytest.raises(ValidationError):
        Extracted(value=1, confidence=0.5, origin=Origin.USER)


def test_round_trips_through_json() -> None:
    e = ev(
        amount_inr="1250.50",
        txn_timestamp="2026-11-10T10:15:00+05:30",
        channel="UPI",
        utr="412345678901",
        card_last4="1234",
    )
    assert Evidence.model_validate_json(e.model_dump_json()) == e


def test_json_schema_generates() -> None:
    # The extractor uses this schema for structured output.
    schema = Evidence.model_json_schema()
    assert "amount_inr" in schema["properties"]
    assert set(Evidence.fact_names()) <= set(schema["properties"])


def test_timestamp_with_offset_kept() -> None:
    utc_plus_1 = timezone(timedelta(hours=1))
    e = ev(txn_timestamp=datetime(2026, 11, 10, 10, 0, tzinfo=utc_plus_1))
    assert e.txn_timestamp.value is not None
    assert e.txn_timestamp.value.utcoffset() == timedelta(hours=1)
