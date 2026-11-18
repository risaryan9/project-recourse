"""Applying the user's answers from the review screen. Pure: no I/O.

Used twice with the same code: by the API (to reject a bad value immediately, with the field
name) and by the confirm activity (to store the result).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from recourse.agents.intake_graph import IntakeResult, apply_corrections
from recourse.cases.assessment import CaseDetails, missing_details
from recourse.evidence.models import Evidence, FactName


class Confirmation(BaseModel):
    """The user's answers. Empty `corrections` means "the extracted facts are right"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    corrections: dict[FactName, JsonValue] = Field(default_factory=dict)
    details: CaseDetails | None = None


class InvalidAnswer(ValueError):
    """A corrected value failed the hard checks. `fields` names them (never the values)."""

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__("Please check: " + ", ".join(fields))


class ReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    intake: IntakeResult
    details: CaseDetails
    missing_details: list[str]

    @property
    def evidence(self) -> Evidence:
        assert self.intake.evidence is not None
        return self.intake.evidence

    @property
    def needs_confirmation(self) -> list[str]:
        return self.intake.needs_user_confirmation

    @property
    def ready(self) -> bool:
        return not self.needs_confirmation and not self.missing_details


def review(
    evidence: Evidence, details: CaseDetails, confirmation: Confirmation, *, now: datetime
) -> ReviewResult:
    """Apply corrections and detail answers, then re-run every check.

    Raises `InvalidAnswer` naming *every* bad field at once (each correction is checked on its
    own first), so the user can fix them all in one go.
    """
    bad: list[str] = []
    for name, value in confirmation.corrections.items():
        try:
            evidence.with_user_value(name, value)
        except ValidationError:
            bad.append(name)
    if bad:
        raise InvalidAnswer(sorted(bad))
    try:
        intake = apply_corrections(evidence, confirmation.corrections, now=now)
    except ValidationError as e:  # only reachable if corrections conflict with each other
        fields = sorted({str(err["loc"][0]) for err in e.errors() if err["loc"]})
        raise InvalidAnswer(fields or ["unknown"]) from e
    merged = details.merged(confirmation.details)
    assert intake.evidence is not None
    return ReviewResult(
        intake=intake, details=merged, missing_details=missing_details(intake.evidence, merged)
    )
