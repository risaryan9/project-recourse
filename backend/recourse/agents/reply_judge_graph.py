"""ReplyJudgeGraph: judge a bank's reply to the customer's complaint against the rules.

    START → screen (pure Python) ──(phishing / spoofed sender / injection)──→ verdict → END
              │
              ▼ clean
            classify (EXTRACT model: fixed labels + a few extracted fields)
              │
              ▼
            verdict (pure Python: compliance against the rule engine's Entitlement) → END

* **No tools.** The model returns one label from a fixed enum plus numbers and a reference;
  it cannot call anything and has no free-text output. What happens next is decided by the
  workflow from `ReplyVerdict.next_action`, and every consequential step still needs the
  user's approval (closing the case, filing with the Ombudsman).
* **The reply is untrusted data.** It is PII-redacted and wrapped as `<untrusted_document>`.
  A reply that fails the screen (phishing signals, a sender outside the bank's domains, or
  text aimed at an AI) never reaches the model: the user is warned and nothing else happens.
* **The model never decides compliance.** It reports what the bank says it paid; code compares
  that with what the rule engine says is owed.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, field_validator

from recourse.llm.base import LLMClient, LLMRequest, ModelRole, UntrustedDocument
from recourse.rules.models import Entitlement
from recourse.security.injection import find_injection
from recourse.security.phishing import domain_allowed, scan

CLASSIFY_PURPOSE = "reply.classify"

# =================================================================================================
# Input / output
# =================================================================================================


class ReplyLabel(StrEnum):
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"  # received / registered / under review; no decision yet
    RESOLVED = "RESOLVED"  # says the money was reversed / refunded / credited in full
    PARTIAL = "PARTIAL"  # says only part was paid, or reversed without the compensation
    REJECTED_WITH_REASONS = "REJECTED_WITH_REASONS"  # declines, and explains why
    REJECTED_NO_REASONS = "REJECTED_NO_REASONS"  # declines without a reason
    NEEDS_INFO = "NEEDS_INFO"  # asks the customer for documents or details
    UNCLEAR = "UNCLEAR"  # none of the above, or not a reply to this complaint


class SenderCheck(StrEnum):
    MATCH = "MATCH"  # the sender's domain is one of the bank's known domains
    MISMATCH = "MISMATCH"  # it is not: possibly spoofed
    UNKNOWN = "UNKNOWN"  # no sender given (pasted reply), or no known domains for the bank


class NextAction(StrEnum):
    WARN = "WARN"  # suspicious message: warn the user, do nothing else, keep waiting
    KEEP_WAITING = "KEEP_WAITING"  # acknowledgement: keep the timer running
    USER_ACTION = "USER_ACTION"  # the bank asks the customer for something; keep waiting
    PROPOSE_RESOLVED = "PROPOSE_RESOLVED"  # paid in full: ask the user to confirm and close
    ESCALATE = "ESCALATE"  # non-compliant: draft the Ombudsman complaint
    ASK_USER = "ASK_USER"  # the user decides: escalate or accept and close


class OwedBasis(StrEnum):
    RULE_ENGINE = "RULE_ENGINE"  # the Entitlement's money lines
    # Fraud with the fault not yet established: the rules fix no amount, so the reply is
    # compared with the disputed transaction amount (the customer's claim, not a rule figure).
    DISPUTED_AMOUNT = "DISPUTED_AMOUNT"


class ReplyClassification(BaseModel):
    """What the model returns. Labels and figures only, no free text."""

    model_config = ConfigDict(extra="forbid")

    label: ReplyLabel
    complaint_number: str | None = Field(
        default=None,
        description="The bank's complaint / ticket / reference number for this complaint, "
        "exactly as written, or null.",
    )
    amount_reversed_inr: Decimal | None = Field(
        default=None, description="Amount the bank says it reversed or refunded, or null."
    )
    compensation_paid_inr: Decimal | None = Field(
        default=None,
        description="Compensation (e.g. for delay) the bank says it paid, or null.",
    )
    reasons_given: bool = Field(
        default=False, description="True if a rejection explains the bank's reasons."
    )


_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{2,39}$")


class ReplyVerdict(BaseModel):
    """The graph's result. Safe to store and show; contains no reply text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: ReplyLabel
    next_action: NextAction
    suspicious: bool
    warnings: list[str] = Field(default_factory=list)  # for the user, from fixed texts
    phishing_signals: list[str] = Field(default_factory=list)
    injection_found: bool = False
    sender_check: SenderCheck
    complaint_number: str | None = None
    amount_reversed_inr: Decimal | None = None
    compensation_paid_inr: Decimal | None = None
    reasons_given: bool = False
    owed_inr: Decimal  # what the reply is checked against (see `owed_basis`)
    owed_basis: OwedBasis = OwedBasis.RULE_ENGINE
    paid_inr: Decimal  # what the bank says it paid (reversal + compensation)
    shortfall_inr: Decimal  # owed - paid, never below 0
    models_used: list[str] = Field(default_factory=list)

    @field_validator("complaint_number")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        return v if v is None or _REF_RE.match(v) else None


class ReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=50_000)  # PII-redacted
    sender: str | None = None  # email address, if known
    bank_domains: list[str] = Field(default_factory=list)
    entitlement: Entitlement
    disputed_amount_inr: Decimal | None = None  # the transaction amount (user-confirmed)

    @property
    def owed(self) -> tuple[Decimal, OwedBasis]:
        """What a reply is checked against, and where that figure comes from."""
        ent = self.entitlement
        if ent.case_type == "fraud" and not ent.money and self.disputed_amount_inr is not None:
            return self.disputed_amount_inr, OwedBasis.DISPUTED_AMOUNT
        return ent.total_owed_inr, OwedBasis.RULE_ENGINE


# =================================================================================================
# Prompt
# =================================================================================================

CLASSIFY_SYSTEM = """\
You read one message that a bank sent to a customer in India about the customer's complaint \
(a failed or unauthorised digital payment). Classify it and extract a few facts.

Labels:
- ACKNOWLEDGEMENT: the complaint was received, registered or is being looked into; no decision.
- RESOLVED: the bank says the full disputed amount was reversed, refunded or credited.
- PARTIAL: the bank says only part was paid, or it reversed the amount but refused or did not \
mention compensation the customer asked for.
- REJECTED_WITH_REASONS: the bank declines the claim and explains why.
- REJECTED_NO_REASONS: the bank declines the claim without explaining why.
- NEEDS_INFO: the bank asks the customer for documents or more details before deciding.
- UNCLEAR: anything else, including messages that are not a reply to this complaint.

Extract only what the message states. amount_reversed_inr and compensation_paid_inr are plain \
numbers in rupees (no symbols), or null if the message does not state them. complaint_number is \
the bank's reference for this complaint exactly as written, or null. Never compute or guess a \
value.
"""

CLASSIFY_TASK = "Classify the bank's message above and extract the facts it states."

# =================================================================================================
# Graph
# =================================================================================================


class ReplyState(TypedDict, total=False):
    inp: ReplyInput
    warnings: list[str]
    phishing_signals: list[str]
    injection_found: bool
    sender_check: SenderCheck
    classification: ReplyClassification
    models_used: list[str]
    verdict: ReplyVerdict


ReplyJudgeGraph = CompiledStateGraph[ReplyState, None, ReplyState, ReplyState]

SPOOF_WARNING = (
    "The sender's address is not one of your bank's known addresses. It may not be from "
    "your bank: check through your bank's official website or app."
)
INJECTION_WARNING = (
    "This message contains text aimed at software rather than at you. A genuine bank reply "
    "would not; Recourse took no action on it."
)
PHISHING_WARNING = (
    "This looks like a scam. Do not share any code or password, do not install any app and "
    "do not pay anything. Report it at cybercrime.gov.in or call 1930."
)


def check_sender(sender: str | None, bank_domains: list[str]) -> SenderCheck:
    if not sender or not bank_domains:
        return SenderCheck.UNKNOWN
    _, _, host = sender.strip().rpartition("@")
    if not host:
        return SenderCheck.UNKNOWN
    return SenderCheck.MATCH if domain_allowed(host, bank_domains) else SenderCheck.MISMATCH


def _verdict(state: ReplyState, label: ReplyLabel, c: ReplyClassification | None) -> ReplyVerdict:
    inp = state["inp"]
    owed, basis = inp.owed
    reversed_ = c.amount_reversed_inr if c else None
    compensation = c.compensation_paid_inr if c else None
    paid = (reversed_ or Decimal("0")) + (compensation or Decimal("0"))
    shortfall = max(Decimal("0"), owed - paid)
    suspicious = bool(state.get("warnings"))

    if suspicious:
        action = NextAction.WARN
    elif label is ReplyLabel.ACKNOWLEDGEMENT:
        action = NextAction.KEEP_WAITING
    elif label is ReplyLabel.NEEDS_INFO:
        action = NextAction.USER_ACTION
    elif label in (ReplyLabel.RESOLVED, ReplyLabel.PARTIAL):
        # Compliance is decided here, from the rule engine's figures, never by the model.
        if shortfall == 0:
            action = NextAction.PROPOSE_RESOLVED
        elif basis is OwedBasis.DISPUTED_AMOUNT:
            # Short of the disputed amount, but the rules fix no figure while the fault is
            # open (the customer may bear part of the loss): flag it, the customer decides.
            action = NextAction.ASK_USER
        else:
            action = NextAction.ESCALATE
    elif label is ReplyLabel.REJECTED_NO_REASONS:
        # An unexplained rejection is an unsatisfactory reply (RB-IOS 10(1)(f)); ADR-014.
        action = NextAction.ESCALATE
    else:  # REJECTED_WITH_REASONS, UNCLEAR: the customer's judgement
        action = NextAction.ASK_USER

    return ReplyVerdict(
        label=label,
        next_action=action,
        suspicious=suspicious,
        warnings=state.get("warnings", []),
        phishing_signals=state.get("phishing_signals", []),
        injection_found=state.get("injection_found", False),
        sender_check=state["sender_check"],
        complaint_number=c.complaint_number if c else None,
        amount_reversed_inr=reversed_,
        compensation_paid_inr=compensation,
        reasons_given=c.reasons_given if c else False,
        owed_inr=owed,
        owed_basis=basis,
        paid_inr=paid,
        shortfall_inr=shortfall,
        models_used=state.get("models_used", []),
    )


def build_reply_judge_graph(llm: LLMClient) -> ReplyJudgeGraph:
    def screen(state: ReplyState) -> dict[str, Any]:
        inp = state["inp"]
        phishing = scan(inp.text, inp.bank_domains)
        injection = find_injection(inp.text)
        sender = check_sender(inp.sender, inp.bank_domains)
        warnings: list[str] = []
        if phishing.suspicious:
            warnings.append(PHISHING_WARNING)
            warnings.extend(phishing.reasons)
        if sender is SenderCheck.MISMATCH:
            warnings.append(SPOOF_WARNING)
        if injection:
            warnings.append(INJECTION_WARNING)
        return {
            "warnings": warnings,
            "phishing_signals": [s.value for s in phishing.signals],
            "injection_found": bool(injection),
            "sender_check": sender,
        }

    def route(state: ReplyState) -> str:
        return "suspicious" if state["warnings"] else "classify"

    async def classify(state: ReplyState) -> dict[str, Any]:
        request = LLMRequest(
            purpose=CLASSIFY_PURPOSE,
            role=ModelRole.EXTRACT,
            system=CLASSIFY_SYSTEM,
            task=CLASSIFY_TASK,
            documents=[UntrustedDocument(kind="bank_reply", text=state["inp"].text)],
            max_tokens=1_000,
        )
        r = await llm.structured(request, ReplyClassification)
        return {"classification": r.output, "models_used": [r.served_model]}

    def judge(state: ReplyState) -> dict[str, Any]:
        c = state["classification"]
        return {"verdict": _verdict(state, c.label, c)}

    def suspicious(state: ReplyState) -> dict[str, Any]:
        return {"verdict": _verdict(state, ReplyLabel.UNCLEAR, None)}

    graph = StateGraph(ReplyState)
    graph.add_node("screen", screen)
    graph.add_node("classify", classify)
    graph.add_node("judge", judge)
    graph.add_node("suspicious", suspicious)
    graph.add_edge(START, "screen")
    graph.add_conditional_edges(
        "screen", route, {"classify": "classify", "suspicious": "suspicious"}
    )
    graph.add_edge("classify", "judge")
    graph.add_edge("judge", END)
    graph.add_edge("suspicious", END)
    return graph.compile()


async def judge_reply(llm: LLMClient, inp: ReplyInput) -> ReplyVerdict:
    """Judge one bank reply. LLM errors (`LLMError`) propagate, as in the other graphs."""
    state = await build_reply_judge_graph(llm).ainvoke({"inp": inp})
    verdict: ReplyVerdict = state["verdict"]
    return verdict
