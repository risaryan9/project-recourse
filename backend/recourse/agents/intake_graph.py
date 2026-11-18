"""IntakeGraph: turn one user submission (text and/or images/PDFs) into checked `Evidence`.

    START → classify ──(UNRELATED)──────────────────────────→ END  status NOT_PAYMENT_EVIDENCE
                 └──→ extract → validate ──(issues)──────────→ END  status NEEDS_CONFIRMATION
                                         └──(all good)───────→ END  status READY

* `classify` and `extract` call the EXTRACT model through the injected `LLMClient` (FakeLLM in
  tests). Everything the user sent is passed as untrusted documents/attachments.
* `validate` has no LLM: it builds `Evidence` (hard checks, PII redaction) and runs
  `check_evidence` (business checks). Fields with errors go back to the user to confirm.
  Nothing is guessed: unreadable values become "missing".
* `now` is part of the input (never read from the clock here), so a run is reproducible and safe
  to call from a Temporal activity.
* LLM errors (`LLMError`) propagate unchanged; the calling activity's retry policy uses their
  `retryable` flag.

After the user confirms or corrects fields, `apply_corrections` re-runs the checks without any
LLM call.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from recourse.agents.intake_schema import Classification, ExtractionOutput, to_evidence
from recourse.evidence.models import IST, Evidence, FactName, SourceKind
from recourse.evidence.validators import EvidenceCheck, check_evidence
from recourse.llm.base import Attachment, LLMClient, LLMRequest, ModelRole, UntrustedDocument
from recourse.security.pii import RedactionKind, redact_text

CLASSIFY_PURPOSE = "intake.classify"
EXTRACT_PURPOSE = "intake.extract"

# =================================================================================================
# Input / output
# =================================================================================================


class IntakeInput(BaseModel):
    """One submission: what the user typed or pasted, plus any uploaded files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = Field(default=None, max_length=20_000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=5)
    # Sensitive data already removed from `text` before it was stored (so the warning survives).
    prior_redactions: list[RedactionKind] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_empty(self) -> IntakeInput:
        if not (self.text and self.text.strip()) and not self.attachments:
            raise ValueError("provide text or at least one attachment")
        return self


class IntakeStatus(StrEnum):
    READY = "READY"  # evidence passed every check; the rule engine can run
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"  # ask the user about `check.needs_user_confirmation`
    NOT_PAYMENT_EVIDENCE = "NOT_PAYMENT_EVIDENCE"  # the input is not about a payment


class IntakeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: IntakeStatus
    evidence: Evidence | None = None
    check: EvidenceCheck | None = None
    # Classifier's one-line explanation (model output: display only, PII-redacted).
    reason: str | None = None
    models_used: list[str] = Field(default_factory=list)

    @property
    def needs_user_confirmation(self) -> list[str]:
        return self.check.needs_user_confirmation if self.check else []


# =================================================================================================
# Prompts (trusted; identical across calls so they are prompt-cached)
# =================================================================================================

CLASSIFY_SYSTEM = """\
You sort what a bank customer submitted to a money-recovery assistant.

The submission may be a screenshot of a UPI or banking app, a bank SMS (typed, pasted or \
photographed), a bank statement PDF, an email from a bank, the customer's own description of \
what happened, or something unrelated.

Choose the single kind that best describes the main evidence:
- SCREENSHOT: an image of an app, website or ATM/terminal screen or receipt.
- SMS: the text of a bank, card or UPI SMS alert.
- STATEMENT_PDF: an account or card statement.
- EMAIL: an email from a bank, payment app or merchant.
- USER_DESCRIPTION: the customer describing the problem in their own words.
- UNRELATED: not about a payment, bank transaction or money lost.

If the customer wrote a description AND attached evidence, classify the attached evidence.
"""

EXTRACT_SYSTEM = """\
You extract facts about ONE disputed payment from a bank customer's evidence, for an assistant \
that helps Indian bank customers recover money.

How to extract:
- Report only what the evidence shows or the customer states. Never guess or fill gaps with \
typical values. If a fact is not there, its value is null.
- For each fact give a confidence from 0.0 to 1.0 and copy the exact words it came from into \
source_text.
- Amounts: copy them as written (e.g. "Rs 2,450.00"). Do not calculate anything.
- Dates and times: ISO 8601 without timezone, in the local time shown. Indian documents write \
dates day-first. Use today's date (given in the task) only to complete a missing year.
- References: the UPI UTR / "UPI Ref No" / IMPS or card RRN goes in utr. App transaction IDs, \
order IDs and complaint numbers go in other_reference.
- Never output full card or account numbers: last 4 digits only. Never copy an OTP, PIN, CVV \
or password anywhere.
- If the evidence shows several transactions, extract the one the customer is complaining \
about; if that is unclear, pick the failed or disputed one, lower the confidences and say so \
in notes.
- case_type and scenario are your interpretation of what went wrong; set a lower confidence if \
the evidence does not make it clear. The customer will confirm them.
"""


def _documents(intake: IntakeInput) -> list[UntrustedDocument]:
    if intake.text and intake.text.strip():
        return [UntrustedDocument(kind="customer_submission", text=intake.text)]
    return []


def _classify_request(intake: IntakeInput) -> LLMRequest:
    return LLMRequest(
        purpose=CLASSIFY_PURPOSE,
        role=ModelRole.EXTRACT,
        system=CLASSIFY_SYSTEM,
        task="Classify the submission above.",
        documents=_documents(intake),
        attachments=intake.attachments,
        max_tokens=1_000,
    )


_KIND_LABEL = {
    SourceKind.SCREENSHOT: "a screenshot",
    SourceKind.SMS: "an SMS",
    SourceKind.STATEMENT_PDF: "a bank statement",
    SourceKind.EMAIL: "an email",
    SourceKind.USER_DESCRIPTION: "the customer's own description",
}


def _extract_request(intake: IntakeInput, kind: SourceKind, now: datetime) -> LLMRequest:
    today = now.astimezone(IST).date().isoformat()
    return LLMRequest(
        purpose=EXTRACT_PURPOSE,
        role=ModelRole.EXTRACT,
        system=EXTRACT_SYSTEM,
        task=(
            f"The evidence above is {_KIND_LABEL[kind]}. "
            f"Today's date is {today} (IST). Extract the facts about the disputed payment."
        ),
        documents=_documents(intake),
        attachments=intake.attachments,
        max_tokens=4_000,
    )


# =================================================================================================
# Graph
# =================================================================================================


class IntakeState(TypedDict, total=False):
    intake: IntakeInput
    now: datetime
    classification: Classification
    extraction: ExtractionOutput
    models_used: list[str]
    result: IntakeResult


IntakeGraph = CompiledStateGraph[IntakeState, None, IntakeState, IntakeState]


def build_intake_graph(llm: LLMClient) -> IntakeGraph:
    async def classify(state: IntakeState) -> dict[str, Any]:
        r = await llm.structured(_classify_request(state["intake"]), Classification)
        return {"classification": r.output, "models_used": [r.served_model]}

    def route(state: IntakeState) -> str:
        return "extract" if state["classification"].source_kind else "not_payment"

    async def not_payment(state: IntakeState) -> dict[str, Any]:
        reason, _ = redact_text(state["classification"].reason[:200])
        result = IntakeResult(
            status=IntakeStatus.NOT_PAYMENT_EVIDENCE,
            reason=reason,
            models_used=state["models_used"],
        )
        return {"result": result}

    async def extract(state: IntakeState) -> dict[str, Any]:
        kind = state["classification"].source_kind
        assert kind is not None
        r = await llm.structured(
            _extract_request(state["intake"], kind, state["now"]), ExtractionOutput
        )
        return {"extraction": r.output, "models_used": [*state["models_used"], r.served_model]}

    def validate(state: IntakeState) -> dict[str, Any]:
        kind = state["classification"].source_kind
        assert kind is not None
        evidence = to_evidence(
            state["extraction"],
            source_kind=kind,
            extracted_by=state["models_used"][-1],
            input_redactions=_input_redactions(state["intake"]),
        )
        return {"result": _checked(evidence, state["now"], models_used=state["models_used"])}

    graph = StateGraph(IntakeState)
    graph.add_node("classify", classify)
    graph.add_node("not_payment", not_payment)
    graph.add_node("extract", extract)
    graph.add_node("validate", validate)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", route, ["extract", "not_payment"])
    graph.add_edge("extract", "validate")
    graph.add_edge("validate", END)
    graph.add_edge("not_payment", END)
    return graph.compile()


async def run_intake(llm: LLMClient, intake: IntakeInput, *, now: datetime) -> IntakeResult:
    """Run the IntakeGraph once. `now` must be timezone-aware (use `workflow.now()` upstream)."""
    _require_aware(now)
    state = await build_intake_graph(llm).ainvoke({"intake": intake, "now": now})
    result: IntakeResult = state["result"]
    return result


# =================================================================================================
# After the user answers
# =================================================================================================


def apply_corrections(
    evidence: Evidence, corrections: Mapping[FactName, Any], *, now: datetime
) -> IntakeResult:
    """Apply values the user typed or confirmed, then re-run every check (no LLM).

    Raises `pydantic.ValidationError` if a user value fails the hard checks (e.g. an amount that
    is not a number), so the API can show the error next to that field.
    """
    _require_aware(now)
    for name, value in corrections.items():
        evidence = evidence.with_user_value(name, value)
    return _checked(evidence, now, models_used=[])


def _checked(evidence: Evidence, now: datetime, *, models_used: list[str]) -> IntakeResult:
    check = check_evidence(evidence, now=now)
    return IntakeResult(
        status=IntakeStatus.READY if check.ok else IntakeStatus.NEEDS_CONFIRMATION,
        evidence=evidence,
        check=check,
        models_used=models_used,
    )


def _input_redactions(intake: IntakeInput) -> set[RedactionKind]:
    """Sensitive data found in the typed/pasted text (removed before any LLM saw it).

    Images and PDFs cannot be scanned here; secrets in them are caught on the extracted output.
    """
    found = set(intake.prior_redactions)
    if intake.text:
        found |= redact_text(intake.text)[1]
    return found


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
