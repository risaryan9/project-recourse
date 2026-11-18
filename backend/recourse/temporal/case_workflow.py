"""CaseWorkflow: one per case, runs for weeks. Orchestration only (CLAUDE.md §3).

Version 1 covers the start of a case:

    run_intake ──(not payment evidence)──────────────────────────────→ ABANDONED
        │
        ▼
    EXTRACTED: wait for `confirm` signals; each one is applied by the confirm activity
        │      (still issues → keep waiting, with the issues in `get_state`)
        ▼
    assess (rule engine) → ASSESSED → wait (drafting is the next milestone)

An `abandon` signal ends the case from any waiting point. Every status change is written to the
database by the activity that causes it (with an audit event), so the API reads the database
and does not depend on Temporal being up.

Determinism rules: no I/O, clock or randomness here. Time comes from `workflow.now()`.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from recourse.agents.intake_graph import IntakeStatus
    from recourse.db.models import Actor, CaseStatus
    from recourse.evidence.models import IST
    from recourse.temporal.case_types import (
        ASSESS,
        CONFIRM,
        INVALID_USER_INPUT,
        MARK_ABANDONED,
        RUN_INTAKE,
        AbandonRequest,
        AssessInput,
        AssessSummary,
        CaseInput,
        CaseOutcome,
        CaseState,
        Confirmation,
        ConfirmInput,
        ConfirmOutcome,
        IntakeSummary,
        MarkAbandonedInput,
        RunIntakeInput,
    )

# LLM steps: the LLM client retries quickly itself; Temporal retries over minutes.
LLM_TIMEOUT = timedelta(minutes=5)
LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
    non_retryable_error_types=[INVALID_USER_INPUT],
)
# Database / rule-engine steps.
DB_TIMEOUT = timedelta(seconds=60)
DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
    non_retryable_error_types=[INVALID_USER_INPUT],
)


def _error_message(err: ActivityError) -> str:
    cause = err.cause
    if isinstance(cause, ApplicationError):
        return cause.message
    return "internal error"


@workflow.defn
class CaseWorkflow:
    def __init__(self) -> None:
        self._status = CaseStatus.NEW
        self._needs_confirmation: list[str] = []
        self._missing_details: list[str] = []
        self._last_error: str | None = None
        self._confirmations: list[Confirmation] = []
        self._abandon: AbandonRequest | None = None

    # --- signals & queries ----------------------------------------------------------------------

    @workflow.signal(name="confirm")
    def confirm(self, confirmation: Confirmation) -> None:
        self._confirmations.append(confirmation)

    @workflow.signal(name="abandon")
    def abandon(self, request: AbandonRequest) -> None:
        if self._abandon is None:
            self._abandon = request

    @workflow.query(name="get_state")
    def get_state(self) -> CaseState:
        return CaseState(
            status=self._status,
            needs_confirmation=self._needs_confirmation,
            missing_details=self._missing_details,
            last_error=self._last_error,
        )

    # --- main flow --------------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: CaseInput) -> CaseOutcome:
        # 1. Intake
        try:
            intake: IntakeSummary = await workflow.execute_activity(
                RUN_INTAKE,
                RunIntakeInput(case_id=inp.case_id, upload_id=inp.upload_id, now=workflow.now()),
                result_type=IntakeSummary,
                start_to_close_timeout=LLM_TIMEOUT,
                retry_policy=LLM_RETRY,
            )
        except ActivityError as err:
            return await self._end(inp, f"intake_failed: {_error_message(err)}", Actor.SYSTEM)
        if intake.status is IntakeStatus.NOT_PAYMENT_EVIDENCE:
            # The activity already recorded ABANDONED with the reason.
            self._status = CaseStatus.ABANDONED
            return CaseOutcome(status=CaseStatus.ABANDONED, reason="not_payment_evidence")

        self._status = CaseStatus.EXTRACTED
        self._needs_confirmation = intake.needs_confirmation
        self._missing_details = intake.missing_details

        # 2. Review loop: the user always reviews the facts once, then until nothing is missing.
        while True:
            await workflow.wait_condition(lambda: bool(self._confirmations) or self._abandoned)
            if self._abandon is not None:
                return await self._end(inp, self._abandon.reason, Actor.USER)
            outcome = await self._apply(inp, self._confirmations.pop(0))
            if outcome is None or not outcome.ready:
                continue

            # 3. Assess with the rule engine (as_of = today's date in India).
            try:
                await workflow.execute_activity(
                    ASSESS,
                    AssessInput(case_id=inp.case_id, as_of=workflow.now().astimezone(IST).date()),
                    result_type=AssessSummary,
                    start_to_close_timeout=DB_TIMEOUT,
                    retry_policy=DB_RETRY,
                )
            except ActivityError as err:
                # Facts the engine cannot use: back to review with the reason.
                self._last_error = _error_message(err)
                continue
            self._status = CaseStatus.ASSESSED
            break

        # 4. Next milestone (drafting) plugs in here. Until then: wait until abandoned.
        await workflow.wait_condition(lambda: self._abandoned)
        assert self._abandon is not None
        return await self._end(inp, self._abandon.reason, Actor.USER)

    # --- helpers --------------------------------------------------------------------------------

    @property
    def _abandoned(self) -> bool:
        return self._abandon is not None

    async def _apply(self, inp: CaseInput, confirmation: Confirmation) -> ConfirmOutcome | None:
        try:
            outcome: ConfirmOutcome = await workflow.execute_activity(
                CONFIRM,
                ConfirmInput(case_id=inp.case_id, confirmation=confirmation, now=workflow.now()),
                result_type=ConfirmOutcome,
                start_to_close_timeout=DB_TIMEOUT,
                retry_policy=DB_RETRY,
            )
        except ActivityError as err:
            self._last_error = _error_message(err)
            return None
        self._last_error = None
        self._needs_confirmation = outcome.needs_confirmation
        self._missing_details = outcome.missing_details
        return outcome

    async def _end(self, inp: CaseInput, reason: str, actor: Actor) -> CaseOutcome:
        await workflow.execute_activity(
            MARK_ABANDONED,
            MarkAbandonedInput(case_id=inp.case_id, reason=reason, actor=actor),
            start_to_close_timeout=DB_TIMEOUT,
            retry_policy=DB_RETRY,
        )
        self._status = CaseStatus.ABANDONED
        return CaseOutcome(status=CaseStatus.ABANDONED, reason=reason)
