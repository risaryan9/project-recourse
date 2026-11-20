"""Golden cases: escalation to the RBI Ombudsman (RB-IOS 2026, clause 10(1)(e)-(g)).

Every expected value was worked out BY HAND from the scheme, never copied from engine output.
  waiting period ends = bank received complaint + max(30, regulatory reply days)
  eligible from       = day after the waiting period ends
                        (or the date of an unsatisfactory reply, if earlier)
  filing deadline     = max(waiting period end, bank's last communication) + 90 days
"""

from datetime import date

import pytest

from recourse.rules.engine import assess_escalation
from recourse.rules.models import (
    BankType,
    Citation,
    EscalationAssessment,
    EscalationFacts,
    FlagCode,
)

COMMERCIAL = BankType.COMMERCIAL_BANK

# A stand-in for the bank-resolution citation that a fraud Entitlement would pass in.
REG_CITATION = Citation(
    doc_id="RBI-2026-RBC-THIRD-AMENDMENT",
    doc_title="RBC Third Amendment Directions, 2026",
    doc_ref="RBI/2026-27/167",
    para="para 76Q",
    url="https://rbi.org.in/Scripts/NotificationUser.aspx?Id=13543&Mode=0",
    quotes=["this timeline shall not exceed 45 calendar days"],
    verified=False,
)


def _esc(**kwargs: object) -> EscalationAssessment:
    base: dict[str, object] = {"entity_type": COMMERCIAL}
    return assess_escalation(EscalationFacts.model_validate(base | kwargs))


def _deadline(a: EscalationAssessment, key: str) -> date:
    return next(d.due for d in a.deadlines if d.key == key)


def test_failed_txn_no_reply_30_days() -> None:
    # Received 3 Nov 2026 + 30 = 3 Dec. Eligible 4 Dec.
    # Deadline 3 Dec + 90: 28 (to 31 Dec) + 31 (Jan) + 28 (Feb) = 87, +3 -> 3 Mar 2027.
    a = _esc(bank_received_complaint_date=date(2026, 11, 3), as_of=date(2026, 11, 16))
    assert a.waiting_period_ends == date(2026, 12, 3)
    assert a.eligible_from == date(2026, 12, 4)
    assert a.filing_deadline == date(2027, 3, 3)
    assert a.can_file_now is False  # still waiting for the bank
    assert _deadline(a, "ombudsman_eligible_from") == date(2026, 12, 4)
    assert _deadline(a, "ombudsman_filing_deadline") == date(2027, 3, 3)
    assert {c.para for c in a.citations} >= {"clause 10(1)(f)", "clause 10(1)(g)"}


@pytest.mark.parametrize(
    ("as_of", "can_file"),
    [
        (date(2026, 12, 3), False),  # last day of the waiting period
        (date(2026, 12, 4), True),  # first eligible day
        (date(2027, 3, 3), True),  # last day of the filing window
        (date(2027, 3, 4), False),  # one day too late
    ],
)
def test_can_file_boundaries(as_of: date, can_file: bool) -> None:
    a = _esc(bank_received_complaint_date=date(2026, 11, 3), as_of=as_of)
    assert a.can_file_now is can_file
    assert a.has_flag(FlagCode.FILING_DEADLINE_PASSED) is (as_of > date(2027, 3, 3))


def test_unsatisfactory_reply_before_30_days_allows_filing_at_once() -> None:
    # Reply on 20 Nov is unsatisfactory: can file from 20 Nov.
    # Deadline: later of (3 Dec, 20 Nov) = 3 Dec, + 90 -> 3 Mar 2027.
    a = _esc(
        bank_received_complaint_date=date(2026, 11, 3),
        last_bank_communication_date=date(2026, 11, 20),
        reply_unsatisfactory=True,
        as_of=date(2026, 11, 20),
    )
    assert a.eligible_from == date(2026, 11, 20)
    assert a.filing_deadline == date(2027, 3, 3)
    assert a.can_file_now is True


def test_late_reply_moves_the_deadline() -> None:
    # No reply within 30 days -> eligible from 4 Dec. Bank's last reply 10 Jan 2027.
    # Deadline 10 Jan + 90: 21 (to 31 Jan) + 28 (Feb) + 31 (Mar) = 80, +10 -> 10 Apr 2027.
    a = _esc(
        bank_received_complaint_date=date(2026, 11, 3),
        last_bank_communication_date=date(2027, 1, 10),
        reply_unsatisfactory=True,
        as_of=date(2027, 1, 12),
    )
    assert a.eligible_from == date(2026, 12, 4)
    assert a.filing_deadline == date(2027, 4, 10)


def test_acknowledgement_after_waiting_period_moves_deadline() -> None:
    # Last communication 20 Feb 2027 (e.g. an acknowledgement, not marked unsatisfactory).
    # Deadline 20 Feb + 90: 8 (to 28 Feb) + 31 (Mar) + 30 (Apr) = 69, +21 -> 21 May 2027.
    a = _esc(
        bank_received_complaint_date=date(2026, 11, 3),
        last_bank_communication_date=date(2027, 2, 20),
        as_of=date(2027, 2, 21),
    )
    assert a.eligible_from == date(2026, 12, 4)
    assert a.filing_deadline == date(2027, 5, 21)


def test_regime_b_fraud_waits_45_days() -> None:
    # "30 days or the RBI timeline, whichever is higher": 45 days for Regime B fraud.
    # Received 2 Mar 2027 + 45 -> 16 Apr. Eligible 17 Apr. Deadline 16 Apr + 90:
    # 14 (to 30 Apr) + 31 (May) + 30 (Jun) = 75, +15 -> 15 Jul 2027.
    a = _esc(
        bank_received_complaint_date=date(2027, 3, 2),
        regulatory_reply_days=45,
        regulatory_reply_citation=REG_CITATION,
        as_of=date(2027, 3, 3),
    )
    assert a.waiting_period_ends == date(2027, 4, 16)
    assert a.eligible_from == date(2027, 4, 17)
    assert a.filing_deadline == date(2027, 7, 15)
    assert REG_CITATION in a.citations


def test_regime_a_fraud_waits_90_days() -> None:
    # Received 5 Nov 2026 + 90 -> 3 Feb 2027. Eligible 4 Feb.
    # Deadline 3 Feb + 90: 25 (to 28 Feb) + 31 (Mar) + 30 (Apr) = 86, +4 -> 4 May 2027.
    a = _esc(
        bank_received_complaint_date=date(2026, 11, 5),
        regulatory_reply_days=90,
        regulatory_reply_citation=REG_CITATION,
        as_of=date(2026, 11, 6),
    )
    assert a.waiting_period_ends == date(2027, 2, 3)
    assert a.eligible_from == date(2027, 2, 4)
    assert a.filing_deadline == date(2027, 5, 4)


def test_shorter_regulatory_timeline_does_not_shorten_the_30_days() -> None:
    a = _esc(
        bank_received_complaint_date=date(2026, 11, 3),
        regulatory_reply_days=10,
        regulatory_reply_citation=REG_CITATION,
        as_of=date(2026, 11, 4),
    )
    assert a.waiting_period_ends == date(2026, 12, 3)


def test_must_complain_to_the_bank_first() -> None:
    a = _esc(bank_received_complaint_date=None, as_of=date(2026, 11, 16))
    assert a.can_file_now is False
    assert a.eligible_from is None and a.filing_deadline is None
    flag = next(f for f in a.flags if f.code is FlagCode.BANK_NOT_YET_COMPLAINED)
    assert flag.citation is not None and flag.citation.para == "clause 10(1)(e)"


@pytest.mark.parametrize(
    ("entity", "coverage"),
    [
        (BankType.COMMERCIAL_BANK, "COVERED"),
        (BankType.SMALL_FINANCE_BANK, "COVERED"),
        (BankType.PAYMENTS_BANK, "COVERED"),
        (BankType.REGIONAL_RURAL_BANK, "COVERED"),
        (BankType.NON_BANK_PPI_ISSUER, "COVERED"),
        (BankType.COOPERATIVE_BANK, "CONDITIONAL"),  # depends on type / deposit size
        (BankType.NBFC, "CONDITIONAL"),  # depends on asset size / deposit-taking
    ],
)
def test_entity_coverage(entity: BankType, coverage: str) -> None:
    a = _esc(
        entity_type=entity,
        bank_received_complaint_date=date(2026, 11, 3),
        as_of=date(2026, 12, 10),
    )
    assert a.entity_coverage == coverage
    assert a.has_flag(FlagCode.ENTITY_COVERAGE_CONDITIONAL) is (coverage == "CONDITIONAL")
    assert a.can_file_now is True


@pytest.mark.parametrize(
    "kwargs",
    [
        # A reply needs a complaint first.
        {"bank_received_complaint_date": None, "last_bank_communication_date": date(2026, 11, 5)},
        # Unsatisfactory needs the reply date.
        {"bank_received_complaint_date": date(2026, 11, 3), "reply_unsatisfactory": True},
        # Reply before the complaint was received.
        {
            "bank_received_complaint_date": date(2026, 11, 3),
            "last_bank_communication_date": date(2026, 11, 2),
        },
        # Days without a citation.
        {"bank_received_complaint_date": date(2026, 11, 3), "regulatory_reply_days": 45},
    ],
)
def test_invalid_escalation_facts_are_rejected(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"entity_type": COMMERCIAL, "as_of": date(2026, 11, 16)}
    with pytest.raises(ValueError):
        EscalationFacts.model_validate(base | kwargs)


def test_unknown_bank_type_keeps_the_dates_but_cannot_file() -> None:
    # Same dates as the 30-day case; only coverage is unknown.
    a = _esc(
        entity_type=None,
        bank_received_complaint_date=date(2026, 11, 3),
        as_of=date(2026, 12, 10),
    )
    assert a.entity_coverage == "UNKNOWN"
    assert a.eligible_from == date(2026, 12, 4)
    assert a.filing_deadline == date(2027, 3, 3)
    assert a.can_file_now is False  # the user must say which kind of bank it is first
    flag = next(f for f in a.flags if f.code is FlagCode.BANK_TYPE_NEEDED)
    assert flag.citation is not None
