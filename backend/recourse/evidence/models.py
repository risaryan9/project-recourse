"""The `Evidence` model: structured facts read from one piece of user evidence.

One `Evidence` object describes what a single upload says: a UPI app screenshot, a bank SMS, a
statement PDF, an email, or the user's own description. The extractor (an LLM) fills it in;
the user can then correct any field.

Design (docs/decisions.md, ADR-003):

* **Every fact is an `Extracted[T]`**: the value, how confident the extractor is (0-1), the exact
  text it was read from (`source_text`, for grounding), and who supplied it (`origin`). A missing
  fact is `value=None`, never a guess.
* **Two layers of checking.**
  1. *Hard (this module, Pydantic)*: the value must have the right type and shape (an enum
     member, a parseable amount, a date). Card/account numbers are cut to their last 4 digits,
     and secrets (OTP, PIN, CVV, passwords) are redacted from every string before the object
     exists, so they can never be stored.
  2. *Soft (`validators.py`)*: business checks (UTR is 12 digits, amount > 0, not in the
     future, fields consistent with each other, confidence high enough). These do not throw;
     they produce issues so the user can be asked to confirm the field.
* **Times are Indian Standard Time.** A timestamp without a timezone is assumed to be IST,
  because that is how Indian bank SMS and UPI apps show it. Dates for the rule engine are the
  IST calendar date (`txn_date`).
* **Money is `Decimal` rupees**, never float. "₹1,250.00", "Rs. 1250" and "INR 1,250" all parse.
* Interpretation fields (`case_type`, `scenario`) are the extractor's reading of what went
  wrong. They are checked and confirmed like any other field before the rule engine sees them.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from recourse.rules.models import Channel, FailedScenario
from recourse.security.pii import RedactionKind, redact_text

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# =================================================================================================
# Enums
# =================================================================================================


class SourceKind(StrEnum):
    """What kind of evidence the facts were read from."""

    SCREENSHOT = "SCREENSHOT"  # UPI app / net-banking / ATM receipt image
    SMS = "SMS"  # bank or UPI SMS text (pasted or screenshotted)
    STATEMENT_PDF = "STATEMENT_PDF"
    EMAIL = "EMAIL"
    USER_DESCRIPTION = "USER_DESCRIPTION"  # the user's own words


class CaseType(StrEnum):
    """Which part of the rulebook applies. Matches `Entitlement.case_type` in the rule engine."""

    FAILED_TRANSACTION = "failed_transaction"
    FRAUD = "fraud"  # unauthorised / fraudulent electronic transaction


class TxnStatus(StrEnum):
    """Status as the document shows it (not our judgement)."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"  # includes "declined"
    PENDING = "PENDING"  # includes "processing", "deemed", "awaiting confirmation"
    REVERSED = "REVERSED"  # money already returned to the customer


class Origin(StrEnum):
    EXTRACTED = "EXTRACTED"  # read by the extractor
    USER = "USER"  # typed or confirmed by the user (treated as fully confident)


# =================================================================================================
# Value types (hard checks and normalisation)
# =================================================================================================

_MONEY_NOISE = re.compile(r"(?i)(₹|rs\.?|inr|/-|,|\s)")


def _parse_money(v: Any) -> Any:
    """'₹1,250.00' / 'Rs. 1250' / 'INR 1,250/-' -> Decimal('1250.00'). Floats go via str."""
    if isinstance(v, float):
        v = repr(v)
    if isinstance(v, str):
        cleaned = _MONEY_NOISE.sub("", v)
        try:
            return Decimal(cleaned)
        except InvalidOperation as e:
            raise ValueError(f"not an amount: {v!r}") from e
    return v


def _to_last4(v: Any) -> Any:
    """Keep only the last 4 digits of a card/account number ('XXXX XXXX 1234' -> '1234')."""
    if v is None:
        return v
    digits = re.sub(r"\D", "", str(v))
    if len(digits) < 4:
        raise ValueError("need at least the last 4 digits")
    return digits[-4:]


def _strip_spaces(v: Any) -> Any:
    return re.sub(r"\s+", "", v) if isinstance(v, str) else v


def _assume_ist(v: datetime) -> datetime:
    return v.replace(tzinfo=IST) if v.tzinfo is None else v


def _clean_text(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


Money = Annotated[Decimal, BeforeValidator(_parse_money), Field(max_digits=14, decimal_places=2)]
Last4 = Annotated[str, BeforeValidator(_to_last4), StringConstraints(pattern=r"^\d{4}$")]
Reference = Annotated[
    str, BeforeValidator(_strip_spaces), StringConstraints(min_length=1, max_length=64)
]
IstDateTime = Annotated[datetime, AfterValidator(_assume_ist)]
ShortText = Annotated[
    str, BeforeValidator(_clean_text), StringConstraints(min_length=1, max_length=200)
]

# =================================================================================================
# Extracted[T]: one fact with its provenance
# =================================================================================================


class Extracted[T](BaseModel):
    """One extracted fact. `value=None` means "not found" (the extractor must not guess)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Verbatim snippet the value was read from, e.g. "UPI Ref No: 412345678901".
    source_text: str | None = Field(default=None, max_length=500)
    origin: Origin = Origin.EXTRACTED

    @model_validator(mode="after")
    def _user_values_are_certain(self) -> Extracted[T]:
        if self.origin is Origin.USER and self.confidence != 1.0:
            raise ValueError("a user-supplied value has confidence 1.0")
        return self

    @property
    def present(self) -> bool:
        return self.value is not None


def _empty() -> Any:
    return Extracted()


# =================================================================================================
# Evidence
# =================================================================================================


class Evidence(BaseModel):
    """Everything one piece of evidence tells us about the transaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- Provenance -------------------------------------------------------------------------------
    source_kind: SourceKind
    extracted_by: str = Field(min_length=1, max_length=100)  # model ID, or "user"
    # Kinds of sensitive data found and removed. Filled automatically; the UI warns the user.
    redactions: list[RedactionKind] = Field(default_factory=list)
    # Extractor remarks, e.g. "image is blurry near the amount". Untrusted text; display only.
    notes: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=10
    )

    # --- What went wrong (interpretation; confirmed by the user) ----------------------------------
    case_type: Extracted[CaseType] = Field(default_factory=_empty)
    scenario: Extracted[FailedScenario] = Field(default_factory=_empty)  # failed transactions only

    # --- The transaction -------------------------------------------------------------------------
    channel: Extracted[Channel] = Field(default_factory=_empty)
    txn_status: Extracted[TxnStatus] = Field(default_factory=_empty)
    account_debited: Extracted[bool] = Field(default_factory=_empty)  # was the customer debited?
    amount_inr: Extracted[Money] = Field(default_factory=_empty)
    txn_timestamp: Extracted[IstDateTime] = Field(default_factory=_empty)
    is_domestic: Extracted[bool] = Field(default_factory=_empty)

    # --- References ------------------------------------------------------------------------------
    utr: Extracted[Reference] = Field(default_factory=_empty)  # UPI UTR / IMPS or card RRN
    other_reference: Extracted[Reference] = Field(default_factory=_empty)  # app txn ID, order ID

    # --- Customer side (payer) -------------------------------------------------------------------
    payer_bank: Extracted[ShortText] = Field(default_factory=_empty)  # name as shown
    payer_account_last4: Extracted[Last4] = Field(default_factory=_empty)
    card_last4: Extracted[Last4] = Field(default_factory=_empty)
    payment_app: Extracted[ShortText] = Field(default_factory=_empty)  # e.g. a UPI app name

    # --- Other side (payee / merchant / ATM) -----------------------------------------------------
    payee_name: Extracted[ShortText] = Field(default_factory=_empty)
    payee_vpa: Extracted[ShortText] = Field(default_factory=_empty)  # UPI ID
    payee_bank: Extracted[ShortText] = Field(default_factory=_empty)
    atm_or_terminal_id: Extracted[ShortText] = Field(default_factory=_empty)

    # --- Reversal (if the money already came back) -----------------------------------------------
    reversal_amount_inr: Extracted[Money] = Field(default_factory=_empty)
    reversal_timestamp: Extracted[IstDateTime] = Field(default_factory=_empty)

    # --- Fraud ------------------------------------------------------------------------------------
    # When the bank's transaction alert (SMS/email) reached the customer. Fraud Regime A counts
    # the reporting delay from this moment.
    alert_received_at: Extracted[IstDateTime] = Field(default_factory=_empty)

    # ---------------------------------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _redact_pii(cls, data: Any) -> Any:
        """Mask card/account numbers and remove secrets from every string, before validation."""
        if not isinstance(data, dict):
            return data
        found: set[RedactionKind] = set()

        def walk(obj: Any) -> Any:
            if isinstance(obj, BaseModel):
                obj = obj.model_dump()
            if isinstance(obj, str):
                text, kinds = redact_text(obj)
                found.update(kinds)
                return text
            if isinstance(obj, dict):
                return {k: walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [walk(v) for v in obj]
            return obj

        cleaned = {k: (v if k == "redactions" else walk(v)) for k, v in data.items()}
        previous = {RedactionKind(r) for r in cleaned.get("redactions", [])}
        cleaned["redactions"] = sorted(previous | found)
        return cleaned

    # --- Convenience -----------------------------------------------------------------------------

    @classmethod
    def fact_names(cls) -> list[str]:
        """Names of all `Extracted` fields, in declaration order."""
        return [
            name
            for name, info in cls.model_fields.items()
            if isinstance(info.annotation, type) and issubclass(info.annotation, Extracted)
        ]

    def fact(self, name: str) -> Extracted[Any]:
        value = getattr(self, name)
        if not isinstance(value, Extracted):
            raise KeyError(f"{name} is not an extracted fact")
        return value

    @property
    def txn_date(self) -> date | None:
        """Transaction date in IST: the rule engine's day T."""
        ts = self.txn_timestamp.value
        return ts.astimezone(IST).date() if ts is not None else None

    @property
    def reversal_date(self) -> date | None:
        ts = self.reversal_timestamp.value
        return ts.astimezone(IST).date() if ts is not None else None

    @property
    def alert_received_date(self) -> date | None:
        ts = self.alert_received_at.value
        return ts.astimezone(IST).date() if ts is not None else None

    def with_user_value(self, name: str, value: Any) -> Evidence:
        """Return a copy where the user has typed or confirmed `name`. Re-runs every hard check."""
        self.fact(name)  # raises KeyError for unknown names
        data = self.model_dump()
        data[name] = {
            "value": value,
            "confidence": 1.0,
            "source_text": None,
            "origin": Origin.USER,
        }
        return Evidence.model_validate(data)


FactName = Literal[
    "case_type",
    "scenario",
    "channel",
    "txn_status",
    "account_debited",
    "amount_inr",
    "txn_timestamp",
    "is_domestic",
    "utr",
    "other_reference",
    "payer_bank",
    "payer_account_last4",
    "card_last4",
    "payment_app",
    "payee_name",
    "payee_vpa",
    "payee_bank",
    "atm_or_terminal_id",
    "reversal_amount_inr",
    "reversal_timestamp",
    "alert_received_at",
]
