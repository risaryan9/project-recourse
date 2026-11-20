"""The deterministic rule engine: the only place money and dates are computed.

Three entry points, all pure functions (no clock, network, randomness or LLM):

* `assess_failed_transaction(facts)` -> `Entitlement`  (2019 TAT and compensation framework)
* `assess_fraud(facts)`              -> `Entitlement`  (customer liability, regimes A and B)
* `assess_escalation(facts)`         -> `EscalationAssessment`  (RB-IOS 2026 filing window)

"Today" is always the `as_of` field of the facts, so the same input gives the same output.
Every money line, deadline and flag carries the `Citation` it is based on.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from recourse.rules.calendar import (
    add_calendar_days,
    add_working_days,
    calendar_days_after,
    working_days_after,
)
from recourse.rules.loader import get_rulebook
from recourse.rules.models import (
    BankType,
    Citation,
    ClockStart,
    DayKind,
    DeadlineLine,
    Entitlement,
    EscalationAssessment,
    EscalationFacts,
    FailedScenario,
    FailedTxnDetails,
    FailedTxnFacts,
    FailedTxnRule,
    Fault,
    Flag,
    FlagCode,
    FraudDetails,
    FraudFacts,
    FraudRegime,
    LiabilityKind,
    LiabilityOutcome,
    MoneyLine,
    NotCoveredReason,
    Rulebook,
    SmallValueCompensation,
    SourceRef,
)

_PAISE = Decimal("0.01")

# Builds a Citation for a SourceRef of the current regime's document.
_CiteFn = Callable[[SourceRef], Citation]


# =================================================================================================
# Helpers
# =================================================================================================


class _Builder:
    """Collects money lines, deadlines and flags, and de-duplicates their citations."""

    def __init__(self, book: Rulebook, as_of: date) -> None:
        self.book = book
        self.as_of = as_of
        self.money: list[MoneyLine] = []
        self.deadlines: list[DeadlineLine] = []
        self.flags: list[Flag] = []
        self._citations: dict[tuple[str, str, tuple[str, ...]], Citation] = {}

    def cite(self, doc_id: str, src: SourceRef, verified: bool) -> Citation:
        doc = self.book.documents[doc_id]
        key = (doc_id, src.para, tuple(src.quotes))
        if key not in self._citations:
            self._citations[key] = Citation(
                doc_id=doc_id,
                doc_title=doc.title,
                doc_ref=doc.ref,
                para=src.para,
                url=doc.url,
                quotes=list(src.quotes),
                verified=verified,
            )
        return self._citations[key]

    def use(self, citation: Citation) -> Citation:
        """Register a citation built elsewhere (e.g. passed in through facts)."""
        key = (citation.doc_id, citation.para, tuple(citation.quotes))
        return self._citations.setdefault(key, citation)

    def add_money(
        self, key: str, label: str, amount: Decimal, citation: Citation, accruing: bool = False
    ) -> None:
        self.money.append(
            MoneyLine(
                key=key,
                label=label,
                amount_inr=amount.quantize(_PAISE),
                accruing=accruing,
                citation=citation,
            )
        )

    def add_deadline(
        self, key: str, label: str, due: date, citation: Citation, approximate: bool = False
    ) -> None:
        self.deadlines.append(
            DeadlineLine(
                key=key,
                label=label,
                due=due,
                passed=due < self.as_of,
                approximate=approximate,
                citation=citation,
            )
        )

    def has_flag(self, code: FlagCode) -> bool:
        return any(f.code == code for f in self.flags)

    def flag(self, code: FlagCode, citation: Citation | None = None) -> None:
        if not self.has_flag(code):
            self.flags.append(Flag(code=code, citation=citation))

    @property
    def citations(self) -> list[Citation]:
        return list(self._citations.values())


def _in_period(d: date, start: date, end: date | None) -> bool:
    return start <= d and (end is None or d <= end)


def _rupees(n: int) -> Decimal:
    return Decimal(n)


# =================================================================================================
# Failed transactions (RBI/2019-20/67)
# =================================================================================================


def assess_failed_transaction(
    facts: FailedTxnFacts, *, rulebook: Rulebook | None = None
) -> Entitlement:
    book = rulebook or get_rulebook()
    regime = book.failed.regime
    general = book.failed.general
    b = _Builder(book, facts.as_of)

    def general_cite(name: str) -> Citation:
        return b.cite(regime.doc, general[name], regime.verified)

    def not_covered(reason: NotCoveredReason, citation: Citation | None) -> Entitlement:
        return Entitlement(
            case_type="failed_transaction",
            as_of=facts.as_of,
            covered=False,
            not_covered_reason=reason,
            regime_id=regime.id if citation is not None else None,
            flags=b.flags,
            citations=b.citations,
        )

    if facts.scenario is FailedScenario.CUSTOMER_ERROR:
        return not_covered(
            NotCoveredReason.CUSTOMER_ERROR, general_cite("failed_transaction_definition")
        )
    if not _in_period(facts.txn_date, regime.effective_from, regime.effective_to):
        return not_covered(NotCoveredReason.BEFORE_FRAMEWORK, general_cite("effective_date"))
    if regime.domestic_only and not facts.domestic:
        return not_covered(NotCoveredReason.NOT_DOMESTIC, general_cite("domestic_only"))

    rule = _match_failed_rule(book, facts)
    if rule is None:
        return not_covered(NotCoveredReason.NO_MATCHING_RULE, None)

    rule_cite = b.cite(regime.doc, rule.source, rule.verified)
    general_cite("t_definition")
    tat_deadline = facts.txn_date + timedelta(days=rule.tat_days)
    reversed_ = facts.reversal_date is not None
    end = facts.reversal_date if facts.reversal_date is not None else facts.as_of
    days_delayed = max(0, calendar_days_after(tat_deadline, end))
    compensation = _rupees(days_delayed * rule.compensation_per_day_inr)

    b.add_deadline("reversal_deadline", "Bank must reverse the debit by", tat_deadline, rule_cite)
    if not reversed_:
        b.add_money("refund", "Reversal of the debited amount", facts.amount_inr, rule_cite)
    if compensation > 0:
        b.add_money(
            "delay_compensation",
            f"Compensation: ₹{rule.compensation_per_day_inr}/day x {days_delayed} days of delay",
            compensation,
            rule_cite,
            accruing=not reversed_,
        )
        general_cite("suo_moto_compensation")
        if not reversed_:
            b.flag(FlagCode.COMPENSATION_ACCRUING, rule_cite)
        if rule.credit_push:
            b.flag(FlagCode.PAYEE_AMBIGUOUS, general_cite("credit_push_payee_principle"))
    if not rule.verified or not regime.verified:
        b.flag(FlagCode.RULE_NOT_VERIFIED)

    return Entitlement(
        case_type="failed_transaction",
        as_of=facts.as_of,
        covered=True,
        regime_id=regime.id,
        rule_id=rule.id,
        money=b.money,
        deadlines=b.deadlines,
        flags=b.flags,
        citations=b.citations,
        details=FailedTxnDetails(
            tat_days=rule.tat_days,
            tat_deadline=tat_deadline,
            reversed=reversed_,
            reversal_date=facts.reversal_date,
            days_delayed=days_delayed,
            compensation_per_day_inr=rule.compensation_per_day_inr,
        ),
    )


def _match_failed_rule(book: Rulebook, facts: FailedTxnFacts) -> FailedTxnRule | None:
    for rule in book.failed.rules:
        if rule.channel is facts.channel and rule.scenario is facts.scenario:
            return rule
    return None


# =================================================================================================
# Fraudulent / unauthorised electronic transactions
# =================================================================================================


def select_fraud_regime(
    txn_date: date, bank_type: BankType, *, rulebook: Rulebook | None = None
) -> tuple[FraudRegime | None, NotCoveredReason | None]:
    """The regime for a transaction date and bank type, or the reason none applies."""
    book = rulebook or get_rulebook()
    in_period = [
        r for r in book.fraud.regimes if _in_period(txn_date, r.effective_from, r.effective_to)
    ]
    if not in_period:
        return None, NotCoveredReason.BEFORE_FRAMEWORK
    for r in in_period:
        if bank_type in r.covered_bank_types:
            return r, None
    return None, NotCoveredReason.BANK_TYPE_NOT_IN_RULEBOOK


def assess_fraud(facts: FraudFacts, *, rulebook: Rulebook | None = None) -> Entitlement:
    book = rulebook or get_rulebook()
    b = _Builder(book, facts.as_of)
    regime, reason = select_fraud_regime(facts.txn_date, facts.bank_type, rulebook=book)

    if regime is None:
        assert reason is not None
        # Fail closed: no liability figures. The UI still shows the urgent 1930 guidance.
        b.flag(FlagCode.REPORT_TO_1930_NCRP)
        return Entitlement(
            case_type="fraud",
            as_of=facts.as_of,
            covered=False,
            not_covered_reason=reason,
            flags=b.flags,
            citations=b.citations,
        )

    def cite(src: SourceRef) -> Citation:
        return b.cite(regime.doc, src, regime.verified)

    tp = regime.third_party

    # --- 1. How late was (or would be) the report to the bank? -------------------------------
    if tp.clock_start is ClockStart.BANK_COMMUNICATION:
        if facts.alert_received_date is None:
            b.flag(FlagCode.ASSUMED_ALERT_ON_TXN_DATE)
        clock_from = facts.alert_received_date or facts.txn_date
    else:
        clock_from = facts.txn_date

    hypothetical = facts.reported_to_bank_date is None
    report_day = facts.reported_to_bank_date or facts.as_of
    if tp.day_kind is DayKind.WORKING:
        delay = working_days_after(clock_from, report_day)
        b.flag(FlagCode.WORKING_DAYS_APPROXIMATE, cite(tp.day_count_source))
        edges = {tp.zero_liability_max_days, tp.zero_liability_max_days + 1}
        if tp.limited_liability_max_days is not None:
            edges |= {tp.limited_liability_max_days, tp.limited_liability_max_days + 1}
        if delay in edges:
            b.flag(FlagCode.BORDERLINE_WORKING_DAYS, cite(tp.day_count_source))
    else:
        delay = max(0, calendar_days_after(clock_from, report_day))
    if hypothetical:
        b.flag(FlagCode.NOT_YET_REPORTED)

    # --- 2. Liability outcome for each possible fault -----------------------------------------
    bank_outcome = LiabilityOutcome(
        fault=Fault.BANK,
        kind=LiabilityKind.ZERO,
        max_customer_liability_inr=Decimal("0.00"),
        citation=cite(regime.bank_fault.source),
    )
    third_party_outcome = _third_party_outcome(regime, facts, delay, cite, b)
    customer_outcome = LiabilityOutcome(
        fault=Fault.CUSTOMER,
        kind=LiabilityKind.CUSTOMER_UNTIL_REPORTED,
        max_customer_liability_inr=facts.amount_inr.quantize(_PAISE),
        citation=cite(regime.customer_negligence.source),
    )
    outcomes = [bank_outcome, third_party_outcome, customer_outcome]

    # --- 3. Small-value compensation (Regime B, customer-negligence cases only) ---------------
    svc = None
    if regime.small_value_compensation is not None and facts.fault in (
        Fault.CUSTOMER,
        Fault.UNKNOWN,
    ):
        svc = _small_value_compensation(regime, facts, cite, b)

    # --- 4. Money the customer is owed, for the stated fault ----------------------------------
    if facts.fault is Fault.UNKNOWN:
        b.flag(FlagCode.FAULT_UNDETERMINED)
        b.flag(FlagCode.BURDEN_OF_PROOF_ON_BANK, cite(regime.burden_of_proof))
    else:
        chosen = {o.fault: o for o in outcomes}[facts.fault]
        if chosen.kind in (LiabilityKind.ZERO, LiabilityKind.LIMITED):
            if chosen.max_customer_liability_inr is not None:
                owed = facts.amount_inr - chosen.max_customer_liability_inr
                if owed > 0:
                    b.add_money(
                        "fraud_reversal",
                        "Reversal of the fraudulent debit"
                        if chosen.kind is LiabilityKind.ZERO
                        else "Reversal of the fraudulent debit, less your capped liability",
                        owed,
                        chosen.citation,
                    )
        if facts.fault is not Fault.BANK:
            b.flag(FlagCode.BURDEN_OF_PROOF_ON_BANK, cite(regime.burden_of_proof))
    if facts.fault in (Fault.CUSTOMER, Fault.UNKNOWN):
        b.flag(FlagCode.LOSS_AFTER_REPORTING_BORNE_BY_BANK, customer_outcome.citation)
    if svc is not None and svc.eligible is True and svc.amount_inr is not None:
        if facts.fault is Fault.CUSTOMER:
            b.add_money(
                "small_value_compensation",
                "Compensation for a small-value fraud (85% of net loss, max ₹25,000)",
                svc.amount_inr,
                svc.citation,
            )

    # --- 5. Deadlines ---------------------------------------------------------------------------
    approx = tp.day_kind is DayKind.WORKING

    def window_end(days: int) -> date:
        if tp.day_kind is DayKind.WORKING:
            return add_working_days(clock_from, days)
        return add_calendar_days(clock_from, days)

    b.add_deadline(
        "report_for_zero_liability",
        "Report to the bank by this date for zero liability (third-party breach)",
        window_end(tp.zero_liability_max_days),
        cite(tp.zero_source),
        approximate=approx,
    )
    if tp.limited_liability_max_days is not None and tp.limited_source is not None:
        b.add_deadline(
            "report_for_limited_liability",
            "Report to the bank by this date for capped (limited) liability",
            window_end(tp.limited_liability_max_days),
            cite(tp.limited_source),
            approximate=approx,
        )
    if facts.reported_to_bank_date is not None:
        complaint = facts.reported_to_bank_date
        res = regime.bank_resolution
        days = res.cross_border_days if facts.cross_border else res.domestic_days
        b.add_deadline(
            "bank_resolution",
            "Bank must resolve your complaint and establish liability by",
            add_calendar_days(complaint, days),
            cite(res.source),
        )
        sr = regime.shadow_reversal
        applies = not sr.credit_card_only or (
            facts.account_type is not None and facts.account_type.is_credit_card
        )
        if applies:
            due = (
                add_working_days(complaint, sr.days)
                if sr.day_kind is DayKind.WORKING
                else add_calendar_days(complaint, sr.days)
            )
            b.add_deadline(
                "shadow_reversal",
                "Bank must provisionally credit (shadow reversal) the amount by",
                due,
                cite(sr.source),
                approximate=sr.day_kind is DayKind.WORKING,
            )

    res = regime.bank_resolution
    reply_days = res.cross_border_days if facts.cross_border else res.domestic_days

    b.flag(
        FlagCode.REPORT_TO_1930_NCRP,
        svc.citation if svc is not None else None,
    )
    if svc is not None:
        b.flag(FlagCode.SMALL_VALUE_COMPENSATION_SUBJECT_TO_BANK, svc.citation)
    if not regime.verified:
        b.flag(FlagCode.RULE_NOT_VERIFIED)

    return Entitlement(
        case_type="fraud",
        as_of=facts.as_of,
        covered=True,
        regime_id=regime.id,
        rule_id=f"{regime.id}:{facts.fault.value}",
        money=b.money,
        deadlines=b.deadlines,
        flags=b.flags,
        citations=b.citations,
        details=FraudDetails(
            fault=facts.fault,
            reporting_delay_days=None if hypothetical else delay,
            reporting_day_kind=tp.day_kind,
            hypothetical_report_today=hypothetical,
            outcomes=outcomes,
            small_value_compensation=svc,
            regulatory_reply_days=reply_days,
            regulatory_reply_citation=cite(res.source),
        ),
    )


def _third_party_outcome(
    regime: FraudRegime,
    facts: FraudFacts,
    delay: int,
    cite: _CiteFn,
    b: _Builder,
) -> LiabilityOutcome:
    tp = regime.third_party
    if delay <= tp.zero_liability_max_days:
        return LiabilityOutcome(
            fault=Fault.THIRD_PARTY,
            kind=LiabilityKind.ZERO,
            max_customer_liability_inr=Decimal("0.00"),
            citation=cite(tp.zero_source),
        )
    if (
        tp.limited_liability_max_days is not None
        and tp.limited_source is not None
        and delay <= tp.limited_liability_max_days
    ):
        caps = regime.liability_caps
        assert caps is not None  # guaranteed by the FraudRegime validator
        cite(caps.source)
        if facts.account_type is None:
            b.flag(FlagCode.ACCOUNT_TYPE_NEEDED, cite(caps.source))
            cap: Decimal | None = None
        else:
            cap = min(facts.amount_inr, _rupees(caps.cap_for(facts.account_type)))
        return LiabilityOutcome(
            fault=Fault.THIRD_PARTY,
            kind=LiabilityKind.LIMITED,
            max_customer_liability_inr=None if cap is None else cap.quantize(_PAISE),
            citation=cite(tp.limited_source),
        )
    return LiabilityOutcome(
        fault=Fault.THIRD_PARTY,
        kind=LiabilityKind.BANK_POLICY,
        max_customer_liability_inr=None,
        citation=cite(tp.beyond_source),
    )


# Condition codes for small-value compensation (the UI turns them into text).
SVC_INDIVIDUAL_ONLY = "INDIVIDUAL_ONLY"
SVC_OCCURRENCE_WINDOW = "OCCURRENCE_WINDOW"
SVC_GROSS_LOSS_LIMIT = "GROSS_LOSS_LIMIT"
SVC_ONCE_PER_LIFETIME = "ONCE_PER_LIFETIME"
SVC_REPORT_TO_BANK = "REPORT_TO_BANK_WITHIN_WINDOW"
SVC_REPORT_TO_NCRP = "REPORT_TO_NCRP_OR_1930_WITHIN_WINDOW"


def _small_value_compensation(
    regime: FraudRegime, facts: FraudFacts, cite: _CiteFn, b: _Builder
) -> SmallValueCompensation:
    rule = regime.small_value_compensation
    assert rule is not None
    citation = cite(rule.source)
    unmet: list[str] = []
    pending: list[str] = []

    if rule.individuals_only and not facts.is_individual:
        unmet.append(SVC_INDIVIDUAL_ONLY)
    if facts.txn_date > rule.occurrence_on_or_before:
        cite(rule.window_source)
        unmet.append(SVC_OCCURRENCE_WINDOW)
    if facts.gross_loss_inr > rule.max_gross_loss_inr:
        unmet.append(SVC_GROSS_LOSS_LIMIT)
    if rule.once_per_lifetime:
        if facts.previously_received_small_value_compensation is True:
            unmet.append(SVC_ONCE_PER_LIFETIME)
        elif facts.previously_received_small_value_compensation is None:
            pending.append(SVC_ONCE_PER_LIFETIME)

    report_by = add_calendar_days(facts.txn_date, rule.report_within_calendar_days)
    checks = [(facts.reported_to_bank_date, SVC_REPORT_TO_BANK)]
    if rule.requires_ncrp_or_1930_report:
        checks.append((facts.reported_to_ncrp_date, SVC_REPORT_TO_NCRP))
    for reported, code in checks:
        if reported is not None:
            if reported > report_by:
                unmet.append(code)
        elif facts.as_of > report_by:
            unmet.append(code)
        else:
            pending.append(code)
    b.add_deadline(
        "report_for_small_value_compensation",
        "Report to the bank AND to 1930 / cybercrime.gov.in by this date to stay eligible for "
        "small-value fraud compensation",
        report_by,
        citation,
    )

    amount: Decimal | None = None
    if not unmet:
        net = facts.gross_loss_inr - facts.recoveries_inr
        pct = (net * rule.percent_of_net_loss / Decimal(100)).quantize(_PAISE, ROUND_HALF_UP)
        amount = min(pct, _rupees(rule.cap_inr)).quantize(_PAISE)

    return SmallValueCompensation(
        eligible=False if unmet else (None if pending else True),
        amount_inr=amount,
        unmet_conditions=unmet,
        pending_conditions=pending,
        citation=citation,
    )


# =================================================================================================
# Escalation to the RBI Ombudsman (RB-IOS 2026)
# =================================================================================================


def assess_escalation(
    facts: EscalationFacts, *, rulebook: Rulebook | None = None
) -> EscalationAssessment:
    book = rulebook or get_rulebook()
    scheme = book.ombudsman.scheme
    b = _Builder(book, facts.as_of)

    def cite(src: SourceRef) -> Citation:
        return b.cite(scheme.doc, src, scheme.verified)

    entities = scheme.covered_entities
    coverage: Literal["COVERED", "CONDITIONAL", "NOT_COVERED", "UNKNOWN"]
    if facts.entity_type is None:
        coverage = "UNKNOWN"
        b.flag(FlagCode.BANK_TYPE_NEEDED, cite(entities.source))
    elif facts.entity_type in entities.covered:
        coverage = "COVERED"
    elif facts.entity_type in entities.conditional:
        coverage = "CONDITIONAL"
        b.flag(FlagCode.ENTITY_COVERAGE_CONDITIONAL, cite(entities.source))
    else:
        coverage = "NOT_COVERED"
        b.flag(FlagCode.ENTITY_NOT_COVERED, cite(entities.source))
    if facts.as_of < scheme.effective_from:
        b.flag(FlagCode.BEFORE_SCHEME, cite(scheme.effective_source))
    if not scheme.verified:
        b.flag(FlagCode.RULE_NOT_VERIFIED)

    def result(
        eligible_from: date | None, waiting_end: date | None, deadline: date | None
    ) -> EscalationAssessment:
        can_file = (
            coverage in ("COVERED", "CONDITIONAL")
            and not b.has_flag(FlagCode.BEFORE_SCHEME)
            and eligible_from is not None
            and deadline is not None
            and eligible_from <= facts.as_of <= deadline
        )
        return EscalationAssessment(
            scheme_id=scheme.id,
            as_of=facts.as_of,
            entity_coverage=coverage,
            can_file_now=can_file,
            eligible_from=eligible_from,
            waiting_period_ends=waiting_end,
            filing_deadline=deadline,
            deadlines=b.deadlines,
            flags=b.flags,
            citations=b.citations,
        )

    received = facts.bank_received_complaint_date
    if received is None:
        b.flag(FlagCode.BANK_NOT_YET_COMPLAINED, cite(scheme.complain_to_bank_first))
        return result(None, None, None)

    wait_cite = cite(scheme.wait_source)
    wait_days = scheme.min_wait_days
    if facts.regulatory_reply_days is not None and facts.regulatory_reply_citation is not None:
        b.use(facts.regulatory_reply_citation)
        wait_days = max(wait_days, facts.regulatory_reply_days)
    waiting_end = add_calendar_days(received, wait_days)

    last = facts.last_bank_communication_date
    if facts.reply_unsatisfactory and last is not None:
        eligible_from = min(last, waiting_end + timedelta(days=1))
    else:
        eligible_from = waiting_end + timedelta(days=1)
    window_start = max(waiting_end, last) if last is not None else waiting_end
    deadline = add_calendar_days(window_start, scheme.filing_window_days)

    filing_cite = cite(scheme.filing_source)
    b.add_deadline(
        "ombudsman_eligible_from",
        "You can complain to the RBI Ombudsman from",
        eligible_from,
        wait_cite,
    )
    b.add_deadline(
        "ombudsman_filing_deadline",
        "Last day to complain to the RBI Ombudsman",
        deadline,
        filing_cite,
    )
    if facts.as_of > deadline:
        b.flag(FlagCode.FILING_DEADLINE_PASSED, filing_cite)
    return result(eligible_from, waiting_end, deadline)
