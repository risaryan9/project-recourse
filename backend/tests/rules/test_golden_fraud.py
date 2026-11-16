"""Golden cases: fraudulent / unauthorised electronic transactions.

Every expected value was worked out BY HAND from the sources, never copied from engine output.

Regime A (txn before 2027): RBI 2017 circular / RBC Directions 2025 paras 67-73.
  Third-party breach, counted in WORKING days after the day the bank's alert was received:
  0-3 -> zero liability; 4-7 -> min(txn value, Table 1 cap); 8+ -> bank policy.
  Approximate working days: not Sundays, 2nd/4th Saturdays, 26 Jan, 15 Aug, 2 Oct.
Regime B (txn on/after 1 Jan 2027, commercial banks): Third Amendment Directions 2026, 76K-76U.
  Third-party breach: reported within 5 CALENDAR days of the txn -> zero; later -> bank policy.
  Small-value compensation (customer negligence only): min(85% of net loss, ₹25,000).

Hand-made calendars used below (Mon-first):
  Nov 2026: Sun 1,8,15,22,29; Sat 7 (1st, open), 14 (2nd, shut), 21 (3rd, open), 28 (4th, shut).
  Oct 2026: Fri 2 Gandhi Jayanti; Sat 3 (1st, open), Sat 10 (2nd, shut); Sun 4, 11.
  Jan 2026: Sat 24 (4th, shut), Sun 25, Mon 26 Republic Day; Sat 31 (5th, open).
"""

from datetime import date
from decimal import Decimal

import pytest

from recourse.rules.engine import assess_fraud, select_fraud_regime
from recourse.rules.models import (
    AccountType,
    BankType,
    DayKind,
    Entitlement,
    Fault,
    FlagCode,
    FraudDetails,
    FraudFacts,
    LiabilityKind,
    NotCoveredReason,
)

COMMERCIAL = BankType.COMMERCIAL_BANK


def _fraud(**kwargs: object) -> Entitlement:
    base: dict[str, object] = {
        "amount_inr": Decimal("50000"),
        "bank_type": COMMERCIAL,
        "account_type": AccountType.SAVINGS,
        "fault": Fault.THIRD_PARTY,
    }
    return assess_fraud(FraudFacts.model_validate(base | kwargs))


def _details(e: Entitlement) -> FraudDetails:
    assert isinstance(e.details, FraudDetails)
    return e.details


def _outcome(e: Entitlement, fault: Fault) -> tuple[LiabilityKind, Decimal | None]:
    o = next(o for o in _details(e).outcomes if o.fault is fault)
    return o.kind, o.max_customer_liability_inr


def _money(e: Entitlement) -> dict[str, Decimal]:
    return {m.key: m.amount_inr for m in e.money}


def _deadline(e: Entitlement, key: str) -> date:
    return next(d.due for d in e.deadlines if d.key == key)


# =================================================================================================
# Regime selection
# =================================================================================================


@pytest.mark.parametrize(
    ("txn", "bank_type", "regime", "reason"),
    [
        (date(2017, 7, 5), COMMERCIAL, None, NotCoveredReason.BEFORE_FRAMEWORK),
        (date(2017, 7, 6), COMMERCIAL, "FRAUD-A-2017", None),
        (date(2025, 11, 27), COMMERCIAL, "FRAUD-A-2017", None),  # day before RBC 2025
        (date(2025, 11, 28), COMMERCIAL, "FRAUD-A-2025", None),  # RBC 2025 issued
        (date(2026, 12, 31), COMMERCIAL, "FRAUD-A-2025", None),  # last day of regime A
        (date(2027, 1, 1), COMMERCIAL, "FRAUD-B-2027", None),  # first day of regime B
        # The 2017 circular was addressed to RRBs, SFBs and payments banks too...
        (date(2025, 6, 1), BankType.SMALL_FINANCE_BANK, "FRAUD-A-2017", None),
        (date(2025, 11, 27), BankType.REGIONAL_RURAL_BANK, "FRAUD-A-2017", None),
        (date(2025, 6, 1), BankType.PAYMENTS_BANK, "FRAUD-A-2017", None),
        # ...but their post-2025 directions are not in our rulebook: fail closed.
        (
            date(2026, 6, 1),
            BankType.SMALL_FINANCE_BANK,
            None,
            NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK,
        ),
        (
            date(2027, 2, 1),
            BankType.PAYMENTS_BANK,
            None,
            NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK,
        ),
        (
            date(2025, 6, 1),
            BankType.LOCAL_AREA_BANK,
            None,
            NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK,
        ),
        (
            date(2026, 6, 1),
            BankType.COOPERATIVE_BANK,
            None,
            NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK,
        ),
        (date(2027, 2, 1), BankType.NBFC, None, NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK),
    ],  # fmt: skip
)
def test_regime_selection(
    txn: date, bank_type: BankType, regime: str | None, reason: NotCoveredReason | None
) -> None:
    selected, why = select_fraud_regime(txn, bank_type)
    assert (selected.id if selected else None) == regime
    assert why is reason


def test_not_covered_fails_closed_but_keeps_1930_guidance() -> None:
    e = _fraud(
        txn_date=date(2027, 2, 1),
        bank_type=BankType.SMALL_FINANCE_BANK,
        as_of=date(2027, 2, 2),
    )
    assert e.covered is False
    assert e.not_covered_reason is NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK
    assert e.money == [] and e.deadlines == [] and e.details is None
    assert e.has_flag(FlagCode.REPORT_TO_1930_NCRP)


# =================================================================================================
# Regime A: third-party breach, working-day buckets. Alert received Mon 2 Nov 2026.
# Working days after 2 Nov: 3(1) 4(2) 5(3) 6(4) 7(5, 1st Sat) 9(6) 10(7) 11(8) 12(9)
# =================================================================================================


@pytest.mark.parametrize(
    ("reported", "delay", "kind", "max_liability", "owed", "borderline"),
    [
        (date(2026, 11, 2), 0, LiabilityKind.ZERO, "0", "50000", False),  # same day as alert
        (date(2026, 11, 4), 2, LiabilityKind.ZERO, "0", "50000", False),
        (date(2026, 11, 5), 3, LiabilityKind.ZERO, "0", "50000", True),  # last zero day
        (date(2026, 11, 6), 4, LiabilityKind.LIMITED, "10000", "40000", True),  # first limited
        (date(2026, 11, 9), 6, LiabilityKind.LIMITED, "10000", "40000", False),
        (date(2026, 11, 10), 7, LiabilityKind.LIMITED, "10000", "40000", True),  # last limited
        (date(2026, 11, 11), 8, LiabilityKind.BANK_POLICY, None, None, True),
        (date(2026, 11, 12), 9, LiabilityKind.BANK_POLICY, None, None, False),
    ],
)
def test_regime_a_third_party_buckets(
    reported: date,
    delay: int,
    kind: LiabilityKind,
    max_liability: str | None,
    owed: str | None,
    borderline: bool,
) -> None:
    e = _fraud(
        txn_date=date(2026, 11, 2),
        alert_received_date=date(2026, 11, 2),
        reported_to_bank_date=reported,
        as_of=date(2026, 11, 16),
    )
    assert e.regime_id == "FRAUD-A-2025"
    d = _details(e)
    assert d.reporting_delay_days == delay
    assert d.reporting_day_kind is DayKind.WORKING
    assert d.hypothetical_report_today is False
    assert _outcome(e, Fault.THIRD_PARTY) == (
        kind,
        None if max_liability is None else Decimal(max_liability),
    )
    assert _money(e).get("fraud_reversal") == (None if owed is None else Decimal(owed))
    assert e.has_flag(FlagCode.BORDERLINE_WORKING_DAYS) is borderline
    assert e.has_flag(FlagCode.WORKING_DAYS_APPROXIMATE)
    # Report-by deadlines: 3rd and 7th working day after the alert.
    assert _deadline(e, "report_for_zero_liability") == date(2026, 11, 5)
    assert _deadline(e, "report_for_limited_liability") == date(2026, 11, 10)


def test_regime_a_weekend_and_second_saturday_extend_the_window() -> None:
    # Alert Fri 13 Nov 2026. Sat 14 (2nd Sat) and Sun 15 are off.
    # Mon 16 (1), Tue 17 (2), Wed 18 (3) -> zero liability up to 18 Nov, though 5 calendar days.
    # Thu 19 (4), Fri 20 (5), Sat 21 (3rd Sat, 6), Mon 23 (7) -> limited up to 23 Nov.
    e = _fraud(
        txn_date=date(2026, 11, 13),
        alert_received_date=date(2026, 11, 13),
        reported_to_bank_date=date(2026, 11, 18),
        as_of=date(2026, 11, 20),
    )
    assert _details(e).reporting_delay_days == 3
    assert _outcome(e, Fault.THIRD_PARTY)[0] is LiabilityKind.ZERO
    assert _deadline(e, "report_for_zero_liability") == date(2026, 11, 18)
    assert _deadline(e, "report_for_limited_liability") == date(2026, 11, 23)


def test_regime_a_national_holiday_is_not_a_working_day() -> None:
    # Alert Thu 1 Oct 2026. Fri 2 Oct (Gandhi Jayanti) off. Sat 3 (1st Sat) = 1, Mon 5 = 2,
    # Tue 6 = 3 -> zero-liability deadline 6 Oct. Wed 7 = 4, Thu 8 = 5, Fri 9 = 6,
    # Sat 10 (2nd Sat) and Sun 11 off, Mon 12 = 7 -> limited deadline 12 Oct.
    e = _fraud(
        txn_date=date(2026, 10, 1),
        alert_received_date=date(2026, 10, 1),
        reported_to_bank_date=date(2026, 10, 7),
        as_of=date(2026, 10, 20),
    )
    assert _details(e).reporting_delay_days == 4
    assert _outcome(e, Fault.THIRD_PARTY) == (LiabilityKind.LIMITED, Decimal("10000.00"))
    assert _deadline(e, "report_for_zero_liability") == date(2026, 10, 6)
    assert _deadline(e, "report_for_limited_liability") == date(2026, 10, 12)


def test_regime_a_republic_day_and_fourth_saturday() -> None:
    # Alert Fri 23 Jan 2026. Sat 24 (4th Sat), Sun 25, Mon 26 (Republic Day) off.
    # Tue 27 = 1, Wed 28 = 2, Thu 29 = 3 -> zero deadline 29 Jan.
    # Fri 30 = 4, Sat 31 (5th Sat) = 5, Mon 2 Feb = 6, Tue 3 Feb = 7 -> limited deadline 3 Feb.
    e = _fraud(
        txn_date=date(2026, 1, 23),
        alert_received_date=date(2026, 1, 23),
        reported_to_bank_date=date(2026, 1, 29),
        as_of=date(2026, 2, 10),
    )
    assert e.regime_id == "FRAUD-A-2025"
    assert _details(e).reporting_delay_days == 3
    assert _deadline(e, "report_for_zero_liability") == date(2026, 1, 29)
    assert _deadline(e, "report_for_limited_liability") == date(2026, 2, 3)


@pytest.mark.parametrize(
    ("account_type", "amount", "max_liability", "owed"),
    [
        (AccountType.BSBD, "20000", "5000", "15000"),
        (AccountType.BSBD, "3000", "3000", None),  # txn value below the cap: nothing owed
        (AccountType.SAVINGS, "50000", "10000", "40000"),
        (AccountType.CREDIT_CARD_UP_TO_5_LAKH, "12000", "10000", "2000"),
        (AccountType.MSME_CURRENT_CC_OD, "80000", "10000", "70000"),
        (AccountType.CREDIT_CARD_ABOVE_5_LAKH, "100000", "25000", "75000"),
        (AccountType.OTHER_CURRENT_CC_OD, "100000", "25000", "75000"),
    ],
)
def test_regime_a_table_1_caps(
    account_type: AccountType, amount: str, max_liability: str, owed: str | None
) -> None:
    # Alert Mon 2 Nov 2026, reported Mon 9 Nov = 6 working days -> limited liability.
    e = _fraud(
        amount_inr=Decimal(amount),
        account_type=account_type,
        txn_date=date(2026, 11, 2),
        alert_received_date=date(2026, 11, 2),
        reported_to_bank_date=date(2026, 11, 9),
        as_of=date(2026, 11, 16),
    )
    assert _outcome(e, Fault.THIRD_PARTY) == (LiabilityKind.LIMITED, Decimal(max_liability))
    assert _money(e).get("fraud_reversal") == (None if owed is None else Decimal(owed))


def test_regime_a_limited_without_account_type_asks_for_it() -> None:
    e = _fraud(
        account_type=None,
        txn_date=date(2026, 11, 2),
        alert_received_date=date(2026, 11, 2),
        reported_to_bank_date=date(2026, 11, 9),
        as_of=date(2026, 11, 16),
    )
    assert _outcome(e, Fault.THIRD_PARTY) == (LiabilityKind.LIMITED, None)
    assert e.has_flag(FlagCode.ACCOUNT_TYPE_NEEDED)
    assert "fraud_reversal" not in _money(e)


def test_regime_a_bank_fault_is_zero_liability_even_if_reported_late() -> None:
    # Reported 30 days later: bank negligence still means zero liability (para 67(1)).
    e = _fraud(
        fault=Fault.BANK,
        txn_date=date(2026, 10, 1),
        alert_received_date=date(2026, 10, 1),
        reported_to_bank_date=date(2026, 10, 31),
        as_of=date(2026, 11, 2),
    )
    assert _outcome(e, Fault.BANK) == (LiabilityKind.ZERO, Decimal("0.00"))
    assert _money(e) == {"fraud_reversal": Decimal("50000.00")}
    assert not e.has_flag(FlagCode.BURDEN_OF_PROOF_ON_BANK)


def test_regime_a_customer_negligence() -> None:
    e = _fraud(
        fault=Fault.CUSTOMER,
        txn_date=date(2026, 11, 2),
        reported_to_bank_date=date(2026, 11, 3),
        as_of=date(2026, 11, 4),
    )
    assert _outcome(e, Fault.CUSTOMER) == (
        LiabilityKind.CUSTOMER_UNTIL_REPORTED,
        Decimal("50000.00"),
    )
    assert _money(e) == {}
    assert _details(e).small_value_compensation is None  # regime A has no such scheme
    assert e.has_flag(FlagCode.LOSS_AFTER_REPORTING_BORNE_BY_BANK)
    assert e.has_flag(FlagCode.BURDEN_OF_PROOF_ON_BANK)


def test_regime_a_bank_deadlines_after_reporting() -> None:
    # Reported Thu 5 Nov 2026.
    # Resolution: 5 Nov + 90 days: 25 (to 30 Nov) + 31 (Dec) + 31 (Jan) = 87 -> 3 Feb 2027.
    # Shadow reversal: 10 working days after 5 Nov: 6,7,9,10,11,12,13,16,17,18 -> 18 Nov.
    e = _fraud(
        txn_date=date(2026, 11, 2),
        alert_received_date=date(2026, 11, 2),
        reported_to_bank_date=date(2026, 11, 5),
        as_of=date(2026, 11, 6),
    )
    assert _deadline(e, "bank_resolution") == date(2027, 2, 3)
    assert _deadline(e, "shadow_reversal") == date(2026, 11, 18)
    assert _details(e).regulatory_reply_days == 90


def test_regime_a_missing_alert_date_assumes_txn_date() -> None:
    e = _fraud(
        txn_date=date(2026, 11, 2),
        reported_to_bank_date=date(2026, 11, 5),
        as_of=date(2026, 11, 6),
    )
    assert e.has_flag(FlagCode.ASSUMED_ALERT_ON_TXN_DATE)
    assert _details(e).reporting_delay_days == 3


def test_regime_a_not_yet_reported_counts_as_if_reported_today() -> None:
    # Alert Mon 2 Nov, today Thu 5 Nov = 3 working days: still zero liability if reported today.
    e = _fraud(
        fault=Fault.UNKNOWN,
        txn_date=date(2026, 11, 2),
        alert_received_date=date(2026, 11, 2),
        as_of=date(2026, 11, 5),
    )
    d = _details(e)
    assert d.hypothetical_report_today is True
    assert d.reporting_delay_days is None
    assert _outcome(e, Fault.THIRD_PARTY)[0] is LiabilityKind.ZERO
    assert e.has_flag(FlagCode.NOT_YET_REPORTED)
    assert e.has_flag(FlagCode.FAULT_UNDETERMINED)
    assert e.has_flag(FlagCode.BURDEN_OF_PROOF_ON_BANK)
    assert e.money == []  # fault unknown: no amount until the facts are known
    assert not any(dl.key == "bank_resolution" for dl in e.deadlines)


def test_regime_a_2017_period_cites_the_2017_circular() -> None:
    e = _fraud(
        fault=Fault.BANK,
        txn_date=date(2024, 5, 10),
        reported_to_bank_date=date(2024, 5, 10),
        as_of=date(2024, 5, 11),
    )
    assert e.regime_id == "FRAUD-A-2017"
    assert {c.doc_id for c in e.citations} == {"RBI-2017-CUSTOMER-LIABILITY"}
    reversal = next(m for m in e.money if m.key == "fraud_reversal")
    assert reversal.citation.para == "para 6(i)"


# =================================================================================================
# Regime B (transactions on/after 1 Jan 2027)
# =================================================================================================


@pytest.mark.parametrize(
    ("reported", "delay", "kind", "owed"),
    [
        (date(2027, 3, 1), 0, LiabilityKind.ZERO, "50000"),
        (date(2027, 3, 6), 5, LiabilityKind.ZERO, "50000"),  # day 5: last zero-liability day
        (date(2027, 3, 7), 6, LiabilityKind.BANK_POLICY, None),
    ],
)
def test_regime_b_third_party_five_calendar_days(
    reported: date, delay: int, kind: LiabilityKind, owed: str | None
) -> None:
    e = _fraud(txn_date=date(2027, 3, 1), reported_to_bank_date=reported, as_of=date(2027, 3, 10))
    assert e.regime_id == "FRAUD-B-2027"
    d = _details(e)
    assert d.reporting_day_kind is DayKind.CALENDAR
    assert d.reporting_delay_days == delay
    assert _outcome(e, Fault.THIRD_PARTY)[0] is kind
    assert _money(e).get("fraud_reversal") == (None if owed is None else Decimal(owed))
    assert _deadline(e, "report_for_zero_liability") == date(2027, 3, 6)
    assert not e.has_flag(FlagCode.WORKING_DAYS_APPROXIMATE)
    # Regime B has no limited-liability tier.
    assert not any(dl.key == "report_for_limited_liability" for dl in e.deadlines)


def test_regime_b_bank_resolution_45_and_60_days() -> None:
    # Reported 2 Mar 2027: +45 -> 29 (to 31 Mar) + 16 -> 16 Apr; +60 -> 29 + 30 (Apr) + 1 -> 1 May.
    domestic = _fraud(
        txn_date=date(2027, 3, 1), reported_to_bank_date=date(2027, 3, 2), as_of=date(2027, 3, 3)
    )
    cross = _fraud(
        txn_date=date(2027, 3, 1),
        reported_to_bank_date=date(2027, 3, 2),
        cross_border=True,
        as_of=date(2027, 3, 3),
    )
    assert _deadline(domestic, "bank_resolution") == date(2027, 4, 16)
    assert _details(domestic).regulatory_reply_days == 45
    assert _deadline(cross, "bank_resolution") == date(2027, 5, 1)
    assert _details(cross).regulatory_reply_days == 60


def test_regime_b_shadow_reversal_only_for_credit_cards() -> None:
    card = _fraud(
        account_type=AccountType.CREDIT_CARD_UP_TO_5_LAKH,
        txn_date=date(2027, 3, 1),
        reported_to_bank_date=date(2027, 3, 2),
        as_of=date(2027, 3, 3),
    )
    savings = _fraud(
        account_type=AccountType.SAVINGS,
        txn_date=date(2027, 3, 1),
        reported_to_bank_date=date(2027, 3, 2),
        as_of=date(2027, 3, 3),
    )
    assert _deadline(card, "shadow_reversal") == date(2027, 3, 7)  # 2 Mar + 5 calendar days
    assert not any(dl.key == "shadow_reversal" for dl in savings.deadlines)


def _svc_case(**kwargs: object) -> Entitlement:
    base: dict[str, object] = {
        "fault": Fault.CUSTOMER,
        "txn_date": date(2027, 3, 1),
        "reported_to_bank_date": date(2027, 3, 2),
        "reported_to_ncrp_date": date(2027, 3, 2),
        "previously_received_small_value_compensation": False,
        "as_of": date(2027, 3, 10),
    }
    return _fraud(**(base | kwargs))


@pytest.mark.parametrize(
    ("gross", "recoveries", "expected"),
    [
        ("20000", "0", "17000.00"),  # 85% of 20,000
        ("40000", "15000", "21250.00"),  # RBI Illustration 1: net 25,000 x 85%
        ("40000", "0", "25000.00"),  # RBI Illustration 2: 34,000 capped at 25,000
        ("29411", "0", "24999.35"),  # 29,411 x 0.85 = 24,999.35, below the cap
        ("29412", "0", "25000.00"),  # 29,412 x 0.85 = 25,000.20 -> capped
        ("50000", "0", "25000.00"),  # exactly the ₹50,000 limit is allowed ("up to")
        ("1000.50", "0", "850.43"),  # 850.425 rounds half-up to 850.43
    ],
)
def test_regime_b_small_value_compensation_amounts(
    gross: str, recoveries: str, expected: str
) -> None:
    e = _svc_case(
        amount_inr=Decimal(gross),
        complaint_gross_loss_inr=Decimal(gross),
        recoveries_inr=Decimal(recoveries),
    )
    svc = _details(e).small_value_compensation
    assert svc is not None
    assert svc.eligible is True
    assert svc.amount_inr == Decimal(expected)
    assert _money(e) == {"small_value_compensation": Decimal(expected)}
    assert svc.citation.para == "para 76T(1)"
    assert e.has_flag(FlagCode.SMALL_VALUE_COMPENSATION_SUBJECT_TO_BANK)


@pytest.mark.parametrize(
    ("kwargs", "unmet"),
    [
        ({"amount_inr": Decimal("50000.01")}, "GROSS_LOSS_LIMIT"),
        ({"is_individual": False}, "INDIVIDUAL_ONLY"),
        ({"previously_received_small_value_compensation": True}, "ONCE_PER_LIFETIME"),
        # NCRP report on day 6 (1 Mar + 6 = 7 Mar) is one day too late.
        ({"reported_to_ncrp_date": date(2027, 3, 7)}, "REPORT_TO_NCRP_OR_1930_WITHIN_WINDOW"),
        ({"reported_to_bank_date": date(2027, 3, 7)}, "REPORT_TO_BANK_WITHIN_WINDOW"),
        # Never reported to NCRP and the 5-day window has closed (today 10 Mar).
        ({"reported_to_ncrp_date": None}, "REPORT_TO_NCRP_OR_1930_WITHIN_WINDOW"),
        # 76U: only frauds occurring up to one year from 1 Jan 2027.
        (
            {
                "txn_date": date(2028, 1, 1),
                "reported_to_bank_date": date(2028, 1, 2),
                "reported_to_ncrp_date": date(2028, 1, 2),
                "as_of": date(2028, 1, 3),
            },
            "OCCURRENCE_WINDOW",
        ),
    ],
)
def test_regime_b_small_value_compensation_conditions(
    kwargs: dict[str, object], unmet: str
) -> None:
    e = _svc_case(**kwargs)
    svc = _details(e).small_value_compensation
    assert svc is not None
    assert svc.eligible is False
    assert unmet in svc.unmet_conditions
    assert svc.amount_inr is None
    assert "small_value_compensation" not in _money(e)


def test_regime_b_small_value_compensation_last_day_of_window() -> None:
    e = _svc_case(
        amount_inr=Decimal("10000"),
        txn_date=date(2027, 12, 31),
        reported_to_bank_date=date(2028, 1, 5),  # 31 Dec + 5 = 5 Jan: still in time
        reported_to_ncrp_date=date(2028, 1, 5),
        as_of=date(2028, 1, 6),
    )
    svc = _details(e).small_value_compensation
    assert svc is not None and svc.eligible is True
    assert svc.amount_inr == Decimal("8500.00")
    assert _deadline(e, "report_for_small_value_compensation") == date(2028, 1, 5)


def test_regime_b_small_value_compensation_pending_while_window_open() -> None:
    # Fraud on 1 Mar 2027, today 3 Mar, not yet reported anywhere, lifetime use unknown.
    e = _fraud(
        fault=Fault.UNKNOWN,
        amount_inr=Decimal("20000"),
        txn_date=date(2027, 3, 1),
        as_of=date(2027, 3, 3),
    )
    svc = _details(e).small_value_compensation
    assert svc is not None
    assert svc.eligible is None
    assert svc.unmet_conditions == []
    assert set(svc.pending_conditions) == {
        "ONCE_PER_LIFETIME",
        "REPORT_TO_BANK_WITHIN_WINDOW",
        "REPORT_TO_NCRP_OR_1930_WITHIN_WINDOW",
    }
    assert svc.amount_inr == Decimal("17000.00")  # what it would be, once conditions are met
    assert e.money == []  # not owed yet
    assert _deadline(e, "report_for_small_value_compensation") == date(2027, 3, 6)
    assert e.has_flag(FlagCode.NOT_YET_REPORTED)
    assert e.has_flag(FlagCode.REPORT_TO_1930_NCRP)


def test_regime_b_small_value_compensation_not_for_third_party_fault() -> None:
    e = _fraud(
        txn_date=date(2027, 3, 1), reported_to_bank_date=date(2027, 3, 2), as_of=date(2027, 3, 3)
    )
    assert _details(e).small_value_compensation is None


def test_regime_b_cites_the_2026_directions() -> None:
    e = _fraud(
        fault=Fault.BANK,
        txn_date=date(2027, 3, 1),
        reported_to_bank_date=date(2027, 4, 1),
        as_of=date(2027, 4, 2),
    )
    reversal = next(m for m in e.money if m.key == "fraud_reversal")
    assert reversal.amount_inr == Decimal("50000.00")
    assert reversal.citation.doc_id == "RBI-2026-RBC-THIRD-AMENDMENT"
    assert reversal.citation.para == "para 76L"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"as_of": date(2027, 2, 28)},  # today before the transaction
        {"reported_to_bank_date": date(2027, 2, 28)},  # reported before the transaction
        {"reported_to_bank_date": date(2027, 3, 11)},  # reported after today
        {"recoveries_inr": Decimal("60000")},  # recovered more than was lost
    ],
)
def test_invalid_fraud_facts_are_rejected(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "amount_inr": Decimal("50000"),
        "txn_date": date(2027, 3, 1),
        "bank_type": COMMERCIAL,
        "as_of": date(2027, 3, 10),
    }
    with pytest.raises(ValueError):
        FraudFacts.model_validate(base | kwargs)
