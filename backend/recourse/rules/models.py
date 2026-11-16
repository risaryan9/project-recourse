"""Typed models for the rule engine.

Two groups:

1. **Rulebook schema** (`Rulebook` and friends). The YAML files in `rulebook/` are parsed into
   these models with `extra="forbid"`, so a typo or a missing field fails loudly at load time.
2. **Engine inputs and outputs** (`FailedTxnFacts`, `FraudFacts`, `EscalationFacts`,
   `Entitlement`, `EscalationAssessment`). Every amount and date in an output carries the
   `Citation` it came from.

Money is `Decimal` in rupees (never float). Dates are calendar dates (`datetime.date`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# =================================================================================================
# Shared enums
# =================================================================================================


class Channel(StrEnum):
    """Payment channel, as rows of the 2019 failed-transaction Annex."""

    ATM = "ATM"  # includes micro-ATMs
    CARD_TO_CARD = "CARD_TO_CARD"
    CARD_POS = "CARD_POS"  # card present, including cash at PoS
    CARD_NOT_PRESENT = "CARD_NOT_PRESENT"  # e-commerce
    IMPS = "IMPS"
    UPI = "UPI"
    AEPS = "AEPS"  # includes Aadhaar Pay
    APBS = "APBS"
    NACH = "NACH"
    PPI_ON_US = "PPI_ON_US"  # off-us PPI transactions use the underlying rail's channel


class FailedScenario(StrEnum):
    CASH_NOT_DISPENSED = "CASH_NOT_DISPENSED"
    BENEFICIARY_NOT_CREDITED = "BENEFICIARY_NOT_CREDITED"
    MERCHANT_NOT_CONFIRMED = "MERCHANT_NOT_CONFIRMED"
    MANDATE_REVOKED_DEBIT = "MANDATE_REVOKED_DEBIT"
    # The customer made the mistake (e.g. paid the wrong UPI ID). Not a "failed transaction".
    CUSTOMER_ERROR = "CUSTOMER_ERROR"


class BankType(StrEnum):
    COMMERCIAL_BANK = "COMMERCIAL_BANK"  # public, private and foreign banks; SBI
    SMALL_FINANCE_BANK = "SMALL_FINANCE_BANK"
    PAYMENTS_BANK = "PAYMENTS_BANK"
    REGIONAL_RURAL_BANK = "REGIONAL_RURAL_BANK"
    LOCAL_AREA_BANK = "LOCAL_AREA_BANK"
    COOPERATIVE_BANK = "COOPERATIVE_BANK"
    NBFC = "NBFC"
    NON_BANK_PPI_ISSUER = "NON_BANK_PPI_ISSUER"


class AccountType(StrEnum):
    """Account types from Table 1 (maximum customer liability, fraud Regime A)."""

    BSBD = "BSBD"
    SAVINGS = "SAVINGS"
    PPI_OR_GIFT_CARD = "PPI_OR_GIFT_CARD"
    MSME_CURRENT_CC_OD = "MSME_CURRENT_CC_OD"
    INDIVIDUAL_CURRENT_CC_OD_UP_TO_25_LAKH = "INDIVIDUAL_CURRENT_CC_OD_UP_TO_25_LAKH"
    CREDIT_CARD_UP_TO_5_LAKH = "CREDIT_CARD_UP_TO_5_LAKH"
    OTHER_CURRENT_CC_OD = "OTHER_CURRENT_CC_OD"
    CREDIT_CARD_ABOVE_5_LAKH = "CREDIT_CARD_ABOVE_5_LAKH"

    @property
    def is_credit_card(self) -> bool:
        return self in (AccountType.CREDIT_CARD_UP_TO_5_LAKH, AccountType.CREDIT_CARD_ABOVE_5_LAKH)


class Fault(StrEnum):
    """Who caused a fraudulent / unauthorised transaction."""

    BANK = "BANK"  # negligence / deficiency on the part of the bank
    THIRD_PARTY = "THIRD_PARTY"  # third-party breach: neither bank nor customer
    CUSTOMER = "CUSTOMER"  # customer negligence (e.g. shared OTP / PIN)
    UNKNOWN = "UNKNOWN"  # not yet established (the burden of proof is on the bank)


class LiabilityKind(StrEnum):
    ZERO = "ZERO"  # customer bears nothing; full reversal
    LIMITED = "LIMITED"  # customer bears at most a capped amount
    BANK_POLICY = "BANK_POLICY"  # decided by the bank's board-approved policy
    CUSTOMER_UNTIL_REPORTED = "CUSTOMER_UNTIL_REPORTED"  # customer bears loss until reporting


class DayKind(StrEnum):
    CALENDAR = "CALENDAR"
    WORKING = "WORKING"


class ClockStart(StrEnum):
    TRANSACTION = "TRANSACTION"  # counted from the date of the transaction
    BANK_COMMUNICATION = "BANK_COMMUNICATION"  # counted from receipt of the bank's alert


class NotCoveredReason(StrEnum):
    BEFORE_FRAMEWORK = "BEFORE_FRAMEWORK"  # transaction predates every regime we have
    NOT_DOMESTIC = "NOT_DOMESTIC"  # failed-txn framework covers domestic transactions only
    CUSTOMER_ERROR = "CUSTOMER_ERROR"  # e.g. money sent to the wrong UPI ID
    NO_MATCHING_RULE = "NO_MATCHING_RULE"  # channel/scenario combination not in the Annex
    BANK_TYPE_NOT_IN_RULEBOOK = "BANK_TYPE_NOT_IN_RULEBOOK"  # fail closed (see decisions.md)


class FlagCode(StrEnum):
    """Machine-readable notes attached to an assessment. The UI turns these into text."""

    RULE_NOT_VERIFIED = "RULE_NOT_VERIFIED"  # a human has not yet ticked `verified: true`
    COMPENSATION_ACCRUING = "COMPENSATION_ACCRUING"  # not reversed yet; grows ₹/day
    PAYEE_AMBIGUOUS = "PAYEE_AMBIGUOUS"  # credit-push principle 1(a) names the beneficiary
    WORKING_DAYS_APPROXIMATE = "WORKING_DAYS_APPROXIMATE"
    BORDERLINE_WORKING_DAYS = "BORDERLINE_WORKING_DAYS"  # within 1 day of a liability bucket edge
    ASSUMED_ALERT_ON_TXN_DATE = "ASSUMED_ALERT_ON_TXN_DATE"
    NOT_YET_REPORTED = "NOT_YET_REPORTED"  # urgent: fraud not yet reported to the bank
    FAULT_UNDETERMINED = "FAULT_UNDETERMINED"
    BURDEN_OF_PROOF_ON_BANK = "BURDEN_OF_PROOF_ON_BANK"
    ACCOUNT_TYPE_NEEDED = "ACCOUNT_TYPE_NEEDED"  # Table 1 cap needs the account type
    LOSS_AFTER_REPORTING_BORNE_BY_BANK = "LOSS_AFTER_REPORTING_BORNE_BY_BANK"
    SMALL_VALUE_COMPENSATION_SUBJECT_TO_BANK = "SMALL_VALUE_COMPENSATION_SUBJECT_TO_BANK"
    REPORT_TO_1930_NCRP = "REPORT_TO_1930_NCRP"
    ENTITY_COVERAGE_CONDITIONAL = "ENTITY_COVERAGE_CONDITIONAL"
    ENTITY_NOT_COVERED = "ENTITY_NOT_COVERED"
    BANK_NOT_YET_COMPLAINED = "BANK_NOT_YET_COMPLAINED"
    FILING_DEADLINE_PASSED = "FILING_DEADLINE_PASSED"
    BEFORE_SCHEME = "BEFORE_SCHEME"  # filing date before RB-IOS 2026 took effect


# =================================================================================================
# Rulebook schema (parsed from YAML)
# =================================================================================================


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Document(_Strict):
    id: str
    title: str
    ref: str
    issued: date
    url: str
    local_file: str
    notes: str | None = None


class SourceRef(_Strict):
    """Where a value comes from: a paragraph of the regime's document, plus exact quotes."""

    para: str
    quotes: list[str] = Field(min_length=1)


class FailedTxnRegime(_Strict):
    id: str
    name: str
    doc: str
    effective_from: date
    effective_to: date | None
    domestic_only: bool
    verified: bool


class FailedTxnRule(_Strict):
    id: str
    channel: Channel
    scenario: FailedScenario
    description: str
    tat_days: int = Field(ge=0)
    compensation_per_day_inr: int = Field(ge=0)
    credit_push: bool
    source: SourceRef
    verified: bool
    notes: str | None = None


class FailedTxnBook(_Strict):
    regime: FailedTxnRegime
    general: dict[str, SourceRef]
    rules: list[FailedTxnRule]
    ppi_off_us: SourceRef


class BankFaultRule(_Strict):
    outcome: Literal[LiabilityKind.ZERO]
    source: SourceRef


class ThirdPartyRule(_Strict):
    clock_start: ClockStart
    day_kind: DayKind
    zero_liability_max_days: int = Field(gt=0)
    zero_source: SourceRef
    limited_liability_max_days: int | None
    limited_source: SourceRef | None
    beyond_outcome: Literal[LiabilityKind.BANK_POLICY]
    beyond_source: SourceRef
    day_count_source: SourceRef

    @model_validator(mode="after")
    def _limited_consistent(self) -> ThirdPartyRule:
        if (self.limited_liability_max_days is None) != (self.limited_source is None):
            raise ValueError("limited_liability_max_days and limited_source go together")
        if (
            self.limited_liability_max_days is not None
            and self.limited_liability_max_days <= self.zero_liability_max_days
        ):
            raise ValueError("limited window must be longer than the zero-liability window")
        return self


class CustomerNegligenceRule(_Strict):
    source: SourceRef


class LiabilityCap(_Strict):
    max_inr: int = Field(gt=0)
    account_types: list[AccountType] = Field(min_length=1)


class LiabilityCaps(_Strict):
    source: SourceRef
    caps: list[LiabilityCap]

    @model_validator(mode="after")
    def _each_account_type_once(self) -> LiabilityCaps:
        seen = [t for cap in self.caps for t in cap.account_types]
        if len(seen) != len(set(seen)):
            raise ValueError("an account type appears in more than one cap")
        if set(seen) != set(AccountType):
            raise ValueError("every account type needs a cap")
        return self

    def cap_for(self, account_type: AccountType) -> int:
        for cap in self.caps:
            if account_type in cap.account_types:
                return cap.max_inr
        raise KeyError(account_type)  # unreachable: validator guarantees full coverage


class ShadowReversalRule(_Strict):
    days: int = Field(gt=0)
    day_kind: DayKind
    credit_card_only: bool
    source: SourceRef


class BankResolutionRule(_Strict):
    domestic_days: int = Field(gt=0)
    cross_border_days: int = Field(gt=0)
    source: SourceRef


class SmallValueCompensationRule(_Strict):
    applies_to_fault: Literal[Fault.CUSTOMER]
    individuals_only: bool
    max_gross_loss_inr: int = Field(gt=0)
    percent_of_net_loss: int = Field(gt=0, le=100)
    cap_inr: int = Field(gt=0)
    report_within_calendar_days: int = Field(gt=0)
    requires_ncrp_or_1930_report: bool
    once_per_lifetime: bool
    occurrence_on_or_before: date
    source: SourceRef
    window_source: SourceRef


class FraudRegime(_Strict):
    id: str
    name: str
    doc: str
    effective_from: date
    effective_to: date | None
    verified: bool
    covered_bank_types: list[BankType] = Field(min_length=1)
    scope: SourceRef
    burden_of_proof: SourceRef
    bank_fault: BankFaultRule
    third_party: ThirdPartyRule
    customer_negligence: CustomerNegligenceRule
    liability_caps: LiabilityCaps | None
    shadow_reversal: ShadowReversalRule
    bank_resolution: BankResolutionRule
    small_value_compensation: SmallValueCompensationRule | None

    @model_validator(mode="after")
    def _caps_iff_limited(self) -> FraudRegime:
        has_limited = self.third_party.limited_liability_max_days is not None
        if has_limited != (self.liability_caps is not None):
            raise ValueError("liability_caps are required exactly when a limited window exists")
        return self


class FraudBook(_Strict):
    regimes: list[FraudRegime] = Field(min_length=1)


class CompensationCaps(_Strict):
    consequential_loss_max_inr: int
    harassment_max_inr: int
    source: SourceRef


class FilingChannels(_Strict):
    portal_url: str
    email: str
    postal_address: str
    source: SourceRef


class CoveredEntities(_Strict):
    covered: list[BankType]
    conditional: list[BankType]
    source: SourceRef

    @model_validator(mode="after")
    def _disjoint(self) -> CoveredEntities:
        if set(self.covered) & set(self.conditional):
            raise ValueError("an entity type cannot be both covered and conditional")
        return self


class OmbudsmanScheme(_Strict):
    id: str
    name: str
    doc: str
    effective_from: date
    effective_to: date | None
    verified: bool
    effective_source: SourceRef
    complain_to_bank_first: SourceRef
    min_wait_days: int = Field(gt=0)
    wait_source: SourceRef
    filing_window_days: int = Field(gt=0)
    filing_source: SourceRef
    no_advocate: SourceRef
    limitation_act: SourceRef
    compensation_caps: CompensationCaps
    award_acceptance_days: int = Field(gt=0)
    award_acceptance_source: SourceRef
    appeal_days: int = Field(gt=0)
    appeal_source: SourceRef
    filing_channels: FilingChannels
    covered_entities: CoveredEntities


class OmbudsmanBook(_Strict):
    scheme: OmbudsmanScheme


class Rulebook(_Strict):
    documents: dict[str, Document]
    failed: FailedTxnBook
    fraud: FraudBook
    ombudsman: OmbudsmanBook


# =================================================================================================
# Engine inputs
# =================================================================================================

PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FailedTxnFacts(_Facts):
    """Facts about one failed transaction. `as_of` is "today": the engine never reads the clock."""

    channel: Channel
    scenario: FailedScenario
    amount_inr: PositiveMoney
    txn_date: date
    reversal_date: date | None = None  # R: the day the money came back, if it has
    domestic: bool = True
    as_of: date

    @model_validator(mode="after")
    def _dates_in_order(self) -> FailedTxnFacts:
        if self.as_of < self.txn_date:
            raise ValueError("as_of cannot be before txn_date")
        if self.reversal_date is not None:
            if self.reversal_date < self.txn_date:
                raise ValueError("reversal_date cannot be before txn_date")
            if self.reversal_date > self.as_of:
                raise ValueError("reversal_date cannot be after as_of")
        return self


class FraudFacts(_Facts):
    """Facts about one fraudulent / unauthorised electronic transaction."""

    amount_inr: PositiveMoney
    txn_date: date
    bank_type: BankType
    account_type: AccountType | None = None
    fault: Fault = Fault.UNKNOWN
    # Regime A counts from receipt of the bank's alert. Defaults to txn_date (flagged).
    alert_received_date: date | None = None
    reported_to_bank_date: date | None = None  # None = not reported yet (urgent mode)
    reported_to_ncrp_date: date | None = None  # cybercrime.gov.in or helpline 1930
    cross_border: bool = False
    # Small-value compensation (Regime B) inputs
    is_individual: bool = True  # an individual person, including a sole proprietor
    complaint_gross_loss_inr: PositiveMoney | None = None  # defaults to amount_inr
    recoveries_inr: NonNegativeMoney = Decimal("0")
    previously_received_small_value_compensation: bool | None = None
    as_of: date

    @model_validator(mode="after")
    def _dates_in_order(self) -> FraudFacts:
        if self.as_of < self.txn_date:
            raise ValueError("as_of cannot be before txn_date")
        for name in ("alert_received_date", "reported_to_bank_date", "reported_to_ncrp_date"):
            value: date | None = getattr(self, name)
            if value is not None and not (self.txn_date <= value <= self.as_of):
                raise ValueError(f"{name} must be between txn_date and as_of")
        # Reporting before the alert arrives is allowed: it counts as a delay of 0 days.
        gross = self.complaint_gross_loss_inr or self.amount_inr
        if self.recoveries_inr > gross:
            raise ValueError("recoveries cannot exceed the gross loss")
        return self

    @property
    def gross_loss_inr(self) -> Decimal:
        return self.complaint_gross_loss_inr or self.amount_inr


class EscalationFacts(_Facts):
    """Facts for working out the RBI Ombudsman escalation window."""

    entity_type: BankType
    bank_received_complaint_date: date | None  # None = not yet complained to the bank
    # Longest reply timeline that RBI / NPCI / card-network rules give the bank for this case
    # (e.g. 45 days for Regime B fraud). Taken from the case's Entitlement. None = no such rule.
    regulatory_reply_days: int | None = Field(default=None, gt=0)
    regulatory_reply_citation: Citation | None = None
    last_bank_communication_date: date | None = None
    reply_unsatisfactory: bool = False
    as_of: date

    @model_validator(mode="after")
    def _consistent(self) -> EscalationFacts:
        if (self.regulatory_reply_days is None) != (self.regulatory_reply_citation is None):
            raise ValueError("regulatory_reply_days needs its citation (and vice versa)")
        if self.bank_received_complaint_date is not None:
            if self.bank_received_complaint_date > self.as_of:
                raise ValueError("bank_received_complaint_date cannot be after as_of")
            last = self.last_bank_communication_date
            if last is not None and not (self.bank_received_complaint_date <= last <= self.as_of):
                raise ValueError("last_bank_communication_date must be between complaint and as_of")
        elif self.last_bank_communication_date is not None or self.reply_unsatisfactory:
            raise ValueError("a bank reply needs a bank complaint first")
        if self.reply_unsatisfactory and self.last_bank_communication_date is None:
            raise ValueError("reply_unsatisfactory needs last_bank_communication_date")
        return self


# =================================================================================================
# Engine outputs
# =================================================================================================


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Citation(_Out):
    doc_id: str
    doc_title: str
    doc_ref: str
    para: str
    url: str
    quotes: list[str]
    verified: bool


class MoneyLine(_Out):
    key: str  # stable identifier, e.g. "refund", "delay_compensation"
    label: str
    amount_inr: Decimal
    accruing: bool = False  # still growing (e.g. ₹100/day until reversed)
    citation: Citation


class DeadlineLine(_Out):
    key: str
    label: str
    due: date
    passed: bool  # due < as_of
    approximate: bool = False  # computed with the approximate working-day calendar
    citation: Citation


class Flag(_Out):
    code: FlagCode
    citation: Citation | None = None


class FailedTxnDetails(_Out):
    kind: Literal["failed_transaction"] = "failed_transaction"
    tat_days: int
    tat_deadline: date
    reversed: bool
    reversal_date: date | None
    days_delayed: int
    compensation_per_day_inr: int


class LiabilityOutcome(_Out):
    fault: Fault
    kind: LiabilityKind
    # The most the customer can be made to bear for this transaction. None = not fixed by the
    # rules (bank policy), or unknown (account type missing for a Table 1 cap).
    max_customer_liability_inr: Decimal | None
    citation: Citation


class SmallValueCompensation(_Out):
    eligible: bool | None  # None = depends on facts not yet known (e.g. not yet reported)
    amount_inr: Decimal | None
    unmet_conditions: list[str]
    pending_conditions: list[str]
    citation: Citation


class FraudDetails(_Out):
    kind: Literal["fraud"] = "fraud"
    fault: Fault
    reporting_delay_days: int | None  # in `reporting_day_kind` units; None if not reported
    reporting_day_kind: DayKind
    hypothetical_report_today: bool  # True: delay computed as if reported on as_of
    outcomes: list[LiabilityOutcome]  # one per known fault: BANK, THIRD_PARTY, CUSTOMER
    small_value_compensation: SmallValueCompensation | None
    regulatory_reply_days: int  # bank's maximum resolution time for this complaint
    regulatory_reply_citation: Citation


class Entitlement(_Out):
    """The rule engine's complete answer for one case. The LLM never computes any of this."""

    case_type: Literal["failed_transaction", "fraud"]
    as_of: date
    covered: bool
    not_covered_reason: NotCoveredReason | None = None
    regime_id: str | None = None
    rule_id: str | None = None
    money: list[MoneyLine] = Field(default_factory=list)
    deadlines: list[DeadlineLine] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)  # every citation used, de-duplicated
    details: FailedTxnDetails | FraudDetails | None = None

    @property
    def total_owed_inr(self) -> Decimal:
        return sum((m.amount_inr for m in self.money), start=Decimal("0"))

    @property
    def all_verified(self) -> bool:
        return all(c.verified for c in self.citations)

    def has_flag(self, code: FlagCode) -> bool:
        return any(f.code == code for f in self.flags)


class EscalationAssessment(_Out):
    scheme_id: str
    as_of: date
    entity_coverage: Literal["COVERED", "CONDITIONAL", "NOT_COVERED"]
    can_file_now: bool
    eligible_from: date | None  # first day a complaint can be filed; None if not complained yet
    waiting_period_ends: date | None
    filing_deadline: date | None
    deadlines: list[DeadlineLine] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    def has_flag(self, code: FlagCode) -> bool:
        return any(f.code == code for f in self.flags)


# `EscalationFacts` refers to `Citation`, which is defined later in this module.
EscalationFacts.model_rebuild()
