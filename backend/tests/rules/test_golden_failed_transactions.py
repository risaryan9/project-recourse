"""Golden cases: failed transactions (RBI/2019-20/67, Annex).

Every expected value was worked out BY HAND from the circular, never copied from engine output.
Formula: deadline = T + TAT; days late = (R or today) - deadline, floored at 0;
compensation = days late x ₹100; refund = amount if not yet reversed.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from recourse.rules.engine import assess_failed_transaction
from recourse.rules.models import (
    Channel,
    DeadlineLine,
    FailedScenario,
    FailedTxnDetails,
    FailedTxnFacts,
    FlagCode,
    MoneyLine,
    NotCoveredReason,
)

C = Channel
S = FailedScenario


@dataclass(frozen=True)
class Golden:
    name: str
    channel: Channel
    scenario: FailedScenario
    amount: str
    txn: date
    as_of: date
    reversal: date | None
    rule_id: str
    deadline: date
    days_late: int
    refund: str  # "0" when already reversed
    compensation: str


GOLDEN = [
    # 1. Running example: UPI P2P, T=1 Nov, deadline 2 Nov, today 10 Nov -> 8 days late.
    Golden("upi_p2p_8_days_late_not_reversed", C.UPI, S.BENEFICIARY_NOT_CREDITED, "5000",
           date(2026, 11, 1), date(2026, 11, 10), None,
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 8, "5000", "800"),
    # 2. Reversed exactly on the deadline day -> on time, nothing owed.
    Golden("upi_p2p_reversed_on_deadline", C.UPI, S.BENEFICIARY_NOT_CREDITED, "5000",
           date(2026, 11, 1), date(2026, 11, 10), date(2026, 11, 2),
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 0, "0", "0"),
    # 3. Reversed one day after the deadline -> ₹100, no refund.
    Golden("upi_p2p_reversed_one_day_late", C.UPI, S.BENEFICIARY_NOT_CREDITED, "5000",
           date(2026, 11, 1), date(2026, 11, 10), date(2026, 11, 3),
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 1, "0", "100"),
    # 4. Today IS the deadline and not reversed yet -> refund due, no compensation yet.
    Golden("upi_p2p_today_is_deadline", C.UPI, S.BENEFICIARY_NOT_CREDITED, "5000",
           date(2026, 11, 1), date(2026, 11, 2), None,
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 0, "5000", "0"),
    # 5. Reported on the same day as the transaction.
    Golden("upi_p2p_same_day", C.UPI, S.BENEFICIARY_NOT_CREDITED, "750",
           date(2026, 11, 1), date(2026, 11, 1), None,
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 0, "750", "0"),
    # 6. UPI merchant: T+5. 1 Nov + 5 = 6 Nov; today 10 Nov -> 4 days.
    Golden("upi_merchant_4_days_late", C.UPI, S.MERCHANT_NOT_CONFIRMED, "1299",
           date(2026, 11, 1), date(2026, 11, 10), None,
           "FT-UPI-MERCHANT-NOT-CONFIRMED", date(2026, 11, 6), 4, "1299", "400"),
    # 7. ATM across a month end: 28 Nov + 5 = 3 Dec; reversed 10 Dec -> 7 days.
    Golden("atm_month_boundary_reversed_late", C.ATM, S.CASH_NOT_DISPENSED, "10000",
           date(2026, 11, 28), date(2026, 12, 15), date(2026, 12, 10),
           "FT-ATM-CASH-NOT-DISPENSED", date(2026, 12, 3), 7, "0", "700"),
    # 8. ATM across a year end: 29 Dec 2026 + 5 = 3 Jan 2027; today 13 Jan -> 10 days.
    Golden("atm_year_boundary", C.ATM, S.CASH_NOT_DISPENSED, "2000",
           date(2026, 12, 29), date(2027, 1, 13), None,
           "FT-ATM-CASH-NOT-DISPENSED", date(2027, 1, 3), 10, "2000", "1000"),
    # 9. Leap year: 28 Feb 2028 + 1 = 29 Feb; reversed 2 Mar -> 2 days.
    Golden("card_to_card_leap_year", C.CARD_TO_CARD, S.BENEFICIARY_NOT_CREDITED, "3000",
           date(2028, 2, 28), date(2028, 3, 5), date(2028, 3, 2),
           "FT-CARD-TO-CARD-NOT-CREDITED", date(2028, 2, 29), 2, "0", "200"),
    # 10. E-commerce (CNP) is T+5 (merged cell with PoS): 1 Oct + 5 = 6 Oct; reversed 26 Oct.
    Golden("card_cnp_20_days_late", C.CARD_NOT_PRESENT, S.MERCHANT_NOT_CONFIRMED, "899",
           date(2026, 10, 1), date(2026, 11, 1), date(2026, 10, 26),
           "FT-CARD-CNP-NOT-CONFIRMED", date(2026, 10, 6), 20, "0", "2000"),
    # 11. Card at PoS: T+5. 10 Nov + 5 = 15 Nov; today 16 Nov -> 1 day.
    Golden("card_pos_1_day_late", C.CARD_POS, S.MERCHANT_NOT_CONFIRMED, "4500",
           date(2026, 11, 10), date(2026, 11, 16), None,
           "FT-CARD-POS-NOT-CONFIRMED", date(2026, 11, 15), 1, "4500", "100"),
    # 12. IMPS: T+1. 30 Nov + 1 = 1 Dec; today 5 Dec -> 4 days.
    Golden("imps_month_end", C.IMPS, S.BENEFICIARY_NOT_CREDITED, "25000",
           date(2026, 11, 30), date(2026, 12, 5), None,
           "FT-IMPS-NOT-CREDITED", date(2026, 12, 1), 4, "25000", "400"),
    # 13. AePS not credited: T+5 (merged cell with 5a). 2 Nov + 5 = 7 Nov; today 9 Nov.
    Golden("aeps_not_credited", C.AEPS, S.BENEFICIARY_NOT_CREDITED, "1000",
           date(2026, 11, 2), date(2026, 11, 9), None,
           "FT-AEPS-NOT-CREDITED", date(2026, 11, 7), 2, "1000", "200"),
    # 14. AePS merchant: T+5. Reversed on time.
    Golden("aeps_merchant_on_time", C.AEPS, S.MERCHANT_NOT_CONFIRMED, "500",
           date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 6),
           "FT-AEPS-MERCHANT-NOT-CONFIRMED", date(2026, 11, 7), 0, "0", "0"),
    # 15. APBS: T+1. 2 Nov + 1 = 3 Nov; today 6 Nov -> 3 days.
    Golden("apbs_3_days", C.APBS, S.BENEFICIARY_NOT_CREDITED, "2500",
           date(2026, 11, 2), date(2026, 11, 6), None,
           "FT-APBS-NOT-CREDITED", date(2026, 11, 3), 3, "2500", "300"),
    # 16. NACH not credited: T+1.
    Golden("nach_not_credited", C.NACH, S.BENEFICIARY_NOT_CREDITED, "15000",
           date(2026, 11, 2), date(2026, 11, 4), None,
           "FT-NACH-NOT-CREDITED", date(2026, 11, 3), 1, "15000", "100"),
    # 17. NACH debit after mandate revoked: T+1, ₹100/day (merged compensation cell with 7a).
    Golden("nach_mandate_revoked", C.NACH, S.MANDATE_REVOKED_DEBIT, "1999",
           date(2026, 11, 5), date(2026, 11, 12), None,
           "FT-NACH-MANDATE-REVOKED", date(2026, 11, 6), 6, "1999", "600"),
    # 18. PPI on-us merchant: T+1. Reversed 4 Nov -> 4 Nov - 3 Nov = 1 day.
    Golden("ppi_on_us_merchant", C.PPI_ON_US, S.MERCHANT_NOT_CONFIRMED, "349",
           date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 4),
           "FT-PPI-ON-US-MERCHANT-NOT-CONFIRMED", date(2026, 11, 3), 1, "0", "100"),
    # 19. PPI on-us not credited: T+1.
    Golden("ppi_on_us_not_credited", C.PPI_ON_US, S.BENEFICIARY_NOT_CREDITED, "100",
           date(2026, 11, 2), date(2026, 11, 3), None,
           "FT-PPI-ON-US-NOT-CREDITED", date(2026, 11, 3), 0, "100", "0"),
    # 20. Long delay: 1 Jan 2026 + 1 = 2 Jan; today 31 Dec 2026 -> 363 days -> ₹36,300.
    Golden("upi_p2p_363_days", C.UPI, S.BENEFICIARY_NOT_CREDITED, "100",
           date(2026, 1, 1), date(2026, 12, 31), None,
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 1, 2), 363, "100", "36300"),
    # 21. Paise in the amount are kept exactly.
    Golden("upi_p2p_paise", C.UPI, S.BENEFICIARY_NOT_CREDITED, "1234.56",
           date(2026, 11, 1), date(2026, 11, 4), None,
           "FT-UPI-P2P-NOT-CREDITED", date(2026, 11, 2), 2, "1234.56", "200"),
    # 22. First day of the framework (effective 15 Oct 2019) is covered.
    Golden("framework_first_day", C.UPI, S.BENEFICIARY_NOT_CREDITED, "500",
           date(2019, 10, 15), date(2019, 10, 20), date(2019, 10, 18),
           "FT-UPI-P2P-NOT-CREDITED", date(2019, 10, 16), 2, "0", "200"),
]  # fmt: skip


@pytest.mark.parametrize("g", GOLDEN, ids=[g.name for g in GOLDEN])
def test_failed_transaction_golden(g: Golden) -> None:
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=g.channel,
            scenario=g.scenario,
            amount_inr=Decimal(g.amount),
            txn_date=g.txn,
            reversal_date=g.reversal,
            as_of=g.as_of,
        )
    )
    assert e.covered and e.not_covered_reason is None
    assert e.regime_id == "FAILED-TXN-2019"
    assert e.rule_id == g.rule_id
    assert isinstance(e.details, FailedTxnDetails)
    assert e.details.tat_deadline == g.deadline
    assert e.details.days_delayed == g.days_late

    money = {m.key: m.amount_inr for m in e.money}
    assert money.get("refund", Decimal("0")) == Decimal(g.refund)
    assert money.get("delay_compensation", Decimal("0")) == Decimal(g.compensation)
    assert e.total_owed_inr == Decimal(g.refund) + Decimal(g.compensation)

    deadline = next(d for d in e.deadlines if d.key == "reversal_deadline")
    assert deadline.due == g.deadline
    assert deadline.passed is (g.deadline < g.as_of)

    # Every money line and deadline cites the 2019 circular.
    lines: list[MoneyLine | DeadlineLine] = [*e.money, *e.deadlines]
    for line in lines:
        assert line.citation.doc_id == "RBI-2019-FAILED-TXN-TAT"
        assert line.citation.para.startswith("Annex, Sl.")
    assert any(c.para == "Annex, General Instructions, item 4" for c in e.citations)


def test_compensation_is_accruing_until_reversed() -> None:
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=C.UPI,
            scenario=S.BENEFICIARY_NOT_CREDITED,
            amount_inr=Decimal("5000"),
            txn_date=date(2026, 11, 1),
            as_of=date(2026, 11, 10),
        )
    )
    comp = next(m for m in e.money if m.key == "delay_compensation")
    assert comp.accruing is True
    assert e.has_flag(FlagCode.COMPENSATION_ACCRUING)


def test_credit_push_compensation_is_flagged_payee_ambiguous() -> None:
    # Principle 1(a) says the penalty for a credit-push transfer goes to the beneficiary.
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=C.IMPS,
            scenario=S.BENEFICIARY_NOT_CREDITED,
            amount_inr=Decimal("5000"),
            txn_date=date(2026, 11, 1),
            as_of=date(2026, 11, 5),
        )
    )
    flag = next(f for f in e.flags if f.code is FlagCode.PAYEE_AMBIGUOUS)
    assert flag.citation is not None
    assert flag.citation.para == "Annex, General Instructions, item 1(a)"


def test_merchant_payment_is_not_flagged_payee_ambiguous() -> None:
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=C.UPI,
            scenario=S.MERCHANT_NOT_CONFIRMED,
            amount_inr=Decimal("5000"),
            txn_date=date(2026, 11, 1),
            as_of=date(2026, 11, 20),
        )
    )
    assert not e.has_flag(FlagCode.PAYEE_AMBIGUOUS)


def test_unverified_rules_are_flagged() -> None:
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=C.ATM,
            scenario=S.CASH_NOT_DISPENSED,
            amount_inr=Decimal("1000"),
            txn_date=date(2026, 11, 1),
            as_of=date(2026, 11, 2),
        )
    )
    assert e.has_flag(FlagCode.RULE_NOT_VERIFIED)
    assert e.all_verified is False


@pytest.mark.parametrize(
    ("channel", "scenario", "txn", "domestic", "reason"),
    [
        # Money sent to the wrong UPI ID is a customer error, not a failed transaction.
        (C.UPI, S.CUSTOMER_ERROR, date(2026, 11, 1), True, NotCoveredReason.CUSTOMER_ERROR),
        # Framework covers domestic transactions only.
        (
            C.CARD_NOT_PRESENT,
            S.MERCHANT_NOT_CONFIRMED,
            date(2026, 11, 1),
            False,
            NotCoveredReason.NOT_DOMESTIC,
        ),
        # Day before the framework took effect (15 Oct 2019).
        (
            C.UPI,
            S.BENEFICIARY_NOT_CREDITED,
            date(2019, 10, 14),
            True,
            NotCoveredReason.BEFORE_FRAMEWORK,
        ),
        # No Annex row for this combination.
        (
            C.ATM,
            S.MERCHANT_NOT_CONFIRMED,
            date(2026, 11, 1),
            True,
            NotCoveredReason.NO_MATCHING_RULE,
        ),
    ],  # fmt: skip
)
def test_not_covered(
    channel: Channel,
    scenario: FailedScenario,
    txn: date,
    domestic: bool,
    reason: NotCoveredReason,
) -> None:
    e = assess_failed_transaction(
        FailedTxnFacts(
            channel=channel,
            scenario=scenario,
            amount_inr=Decimal("1000"),
            txn_date=txn,
            domestic=domestic,
            as_of=date(2026, 11, 16),
        )
    )
    assert e.covered is False
    assert e.not_covered_reason is reason
    assert e.money == [] and e.deadlines == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"as_of": date(2026, 10, 31)},  # today before the transaction
        {"reversal_date": date(2026, 10, 31)},  # reversed before the transaction
        {"reversal_date": date(2026, 11, 20)},  # reversed after today
        {"amount_inr": Decimal("0")},
        {"amount_inr": Decimal("-5")},
        {"amount_inr": Decimal("10.001")},  # more than 2 decimal places
    ],
)
def test_invalid_facts_are_rejected(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "channel": C.UPI,
        "scenario": S.BENEFICIARY_NOT_CREDITED,
        "amount_inr": Decimal("100"),
        "txn_date": date(2026, 11, 1),
        "as_of": date(2026, 11, 10),
    }
    with pytest.raises(ValueError):
        FailedTxnFacts.model_validate(base | kwargs)


def test_same_input_same_output() -> None:
    facts = FailedTxnFacts(
        channel=C.UPI,
        scenario=S.BENEFICIARY_NOT_CREDITED,
        amount_inr=Decimal("5000"),
        txn_date=date(2026, 11, 1),
        as_of=date(2026, 11, 10),
    )
    assert assess_failed_transaction(facts) == assess_failed_transaction(facts)
