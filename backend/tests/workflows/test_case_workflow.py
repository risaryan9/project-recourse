"""CaseWorkflow on Temporal's time-skipping test server, with fake activities (no LLM, no DB)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from recourse.agents.draft_graph import DraftStatus
from recourse.agents.intake_graph import IntakeStatus
from recourse.cases.assessment import CaseDetails
from recourse.cases.drafts import MAX_DRAFTS_PER_CASE
from recourse.db.models import Actor, CaseStatus
from recourse.rules.models import BankType
from recourse.temporal.case_types import (
    APPROVE_DRAFT,
    ASSESS,
    CONFIRM,
    DRAFT,
    INVALID_USER_INPUT,
    MARK_ABANDONED,
    REJECT_DRAFT,
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
    DraftDecision,
    DraftDecisionInput,
    DraftInput,
    DraftSummary,
    IntakeSummary,
    MarkAbandonedInput,
    RunIntakeInput,
)
from recourse.temporal.case_workflow import CaseWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

TASK_QUEUE = "test-cases"


def bad_input(message: str) -> ApplicationError:
    return ApplicationError(message, type=INVALID_USER_INPUT, non_retryable=True)


@dataclass
class FakeActivities:
    """Scripted activity results. Each script entry is a value to return or an exception."""

    intake: list[Any] = field(default_factory=lambda: [IntakeSummary(status=IntakeStatus.READY)])
    confirm: list[Any] = field(default_factory=lambda: [ConfirmOutcome()])
    assess: list[Any] = field(
        default_factory=lambda: [AssessSummary(covered=True, total_owed_inr=Decimal("2650"))]
    )
    draft: list[Any] = field(default_factory=lambda: [drafted()])
    approve: list[Any] = field(default_factory=lambda: [None])
    reject: list[Any] = field(default_factory=lambda: [None])
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def _next(self, name: str, script: list[Any], arg: Any) -> Any:
        self.calls.append((name, arg))
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def activities(self) -> list[Callable[..., Any]]:
        @activity.defn(name=RUN_INTAKE)
        async def run_intake(inp: RunIntakeInput) -> IntakeSummary:
            result: IntakeSummary = self._next(RUN_INTAKE, self.intake, inp)
            return result

        @activity.defn(name=CONFIRM)
        async def confirm(inp: ConfirmInput) -> ConfirmOutcome:
            result: ConfirmOutcome = self._next(CONFIRM, self.confirm, inp)
            return result

        @activity.defn(name=ASSESS)
        async def assess(inp: AssessInput) -> AssessSummary:
            result: AssessSummary = self._next(ASSESS, self.assess, inp)
            return result

        @activity.defn(name=DRAFT)
        async def draft(inp: DraftInput) -> DraftSummary:
            result: DraftSummary = self._next(DRAFT, self.draft, inp)
            return result

        @activity.defn(name=APPROVE_DRAFT)
        async def approve_draft(inp: DraftDecisionInput) -> None:
            self._next(APPROVE_DRAFT, self.approve, inp)

        @activity.defn(name=REJECT_DRAFT)
        async def reject_draft(inp: DraftDecisionInput) -> None:
            self._next(REJECT_DRAFT, self.reject, inp)

        @activity.defn(name=MARK_ABANDONED)
        async def mark_abandoned(inp: MarkAbandonedInput) -> None:
            self.calls.append((MARK_ABANDONED, inp))

        return [run_intake, confirm, assess, draft, approve_draft, reject_draft, mark_abandoned]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def drafted(status: DraftStatus = DraftStatus.DRAFTED) -> DraftSummary:
    return DraftSummary(draft_id=uuid.uuid4(), status=status, rules_verified=False)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as e:
        yield e


async def wait_for(
    handle: WorkflowHandle[Any, Any], check: Callable[[CaseState], bool]
) -> CaseState:
    for _ in range(200):
        state: CaseState = await handle.query(CaseWorkflow.get_state)
        if check(state):
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"state never matched; last: {state}")


def case_input() -> CaseInput:
    return CaseInput(case_id=uuid.uuid4(), upload_id=uuid.uuid4())


async def start(env: WorkflowEnvironment, inp: CaseInput) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        CaseWorkflow.run, inp, id=f"case-{inp.case_id}", task_queue=TASK_QUEUE
    )


def worker(env: WorkflowEnvironment, fakes: FakeActivities) -> Worker:
    return Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[CaseWorkflow],
        activities=fakes.activities(),
    )


async def test_happy_path_to_awaiting_approval(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(
        intake=[IntakeSummary(status=IntakeStatus.NEEDS_CONFIRMATION, needs_confirmation=["utr"])]
    )
    inp = case_input()
    async with worker(env, fakes):
        handle = await start(env, inp)
        state = await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)
        assert state.needs_confirmation == ["utr"]

        await handle.signal(CaseWorkflow.confirm, Confirmation(corrections={"utr": "631845207719"}))
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)

        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        outcome: CaseOutcome = await handle.result()
        history = await handle.fetch_history()

    assert outcome.status is CaseStatus.ABANDONED
    assert fakes.names() == [RUN_INTAKE, CONFIRM, ASSESS, DRAFT, MARK_ABANDONED]
    intake_arg: RunIntakeInput = fakes.calls[0][1]
    assert intake_arg.case_id == inp.case_id and intake_arg.upload_id == inp.upload_id
    confirm_arg: ConfirmInput = fakes.calls[1][1]
    assert confirm_arg.confirmation.corrections == {"utr": "631845207719"}
    assess_arg: AssessInput = fakes.calls[2][1]
    assert isinstance(assess_arg.as_of, date)
    draft_arg: DraftInput = fakes.calls[3][1]
    assert draft_arg.seq == 1 and draft_arg.letter_date == assess_arg.as_of
    abandon_arg: MarkAbandonedInput = fakes.calls[4][1]
    assert abandon_arg.actor is Actor.USER

    # Determinism: the recorded history replays against the current workflow code.
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_not_payment_evidence_ends_the_case(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(intake=[IntakeSummary(status=IntakeStatus.NOT_PAYMENT_EVIDENCE)])
    async with worker(env, fakes):
        handle = await start(env, case_input())
        outcome: CaseOutcome = await handle.result()
    assert outcome == CaseOutcome(status=CaseStatus.ABANDONED, reason="not_payment_evidence")
    assert fakes.names() == [RUN_INTAKE]  # the intake activity recorded it already


async def test_keeps_waiting_until_nothing_is_missing(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(
        confirm=[ConfirmOutcome(missing_details=["bank_type"]), ConfirmOutcome()]
    )
    async with worker(env, fakes):
        handle = await start(env, case_input())
        await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)

        await handle.signal(CaseWorkflow.confirm, Confirmation())
        state = await wait_for(handle, lambda s: s.missing_details == ["bank_type"])
        assert state.status is CaseStatus.EXTRACTED

        details = CaseDetails(bank_type=BankType.COMMERCIAL_BANK)
        await handle.signal(CaseWorkflow.confirm, Confirmation(details=details))
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names().count(ASSESS) == 1


async def test_invalid_answer_is_reported_and_the_case_keeps_waiting(
    env: WorkflowEnvironment,
) -> None:
    fakes = FakeActivities(confirm=[bad_input("amount_inr: not an amount"), ConfirmOutcome()])
    async with worker(env, fakes):
        handle = await start(env, case_input())
        await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)

        await handle.signal(CaseWorkflow.confirm, Confirmation(corrections={"amount_inr": "x"}))
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert state.last_error == "amount_inr: not an amount"
        assert state.status is CaseStatus.EXTRACTED

        await handle.signal(CaseWorkflow.confirm, Confirmation(corrections={"amount_inr": "500"}))
        state = await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        assert state.last_error is None
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()


async def test_assessment_problem_goes_back_to_review(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(
        confirm=[ConfirmOutcome(), ConfirmOutcome()],
        assess=[
            bad_input("reversal_date cannot be before txn_date"),
            AssessSummary(covered=True, total_owed_inr=Decimal("100")),
        ],
    )
    async with worker(env, fakes):
        handle = await start(env, case_input())
        await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)
        await handle.signal(CaseWorkflow.confirm, Confirmation())
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert "reversal_date" in (state.last_error or "")
        assert state.status is CaseStatus.EXTRACTED

        await handle.signal(CaseWorkflow.confirm, Confirmation())
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names().count(ASSESS) == 2


async def test_abandon_while_waiting_for_review(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await start(env, case_input())
        await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest(reason="resolved_elsewhere"))
        outcome: CaseOutcome = await handle.result()
    assert outcome.reason == "resolved_elsewhere"
    assert fakes.names() == [RUN_INTAKE, MARK_ABANDONED]


async def test_intake_failure_ends_the_case(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(intake=[bad_input("model declined")])
    async with worker(env, fakes):
        handle = await start(env, case_input())
        outcome: CaseOutcome = await handle.result()
    assert outcome.status is CaseStatus.ABANDONED
    assert outcome.reason == "intake_failed: model declined"
    abandon_arg: MarkAbandonedInput = fakes.calls[-1][1]
    assert abandon_arg.actor is Actor.SYSTEM


# =================================================================================================
# Drafting and approval
# =================================================================================================


async def to_approval(env: WorkflowEnvironment, fakes: FakeActivities) -> WorkflowHandle[Any, Any]:
    handle = await start(env, case_input())
    await wait_for(handle, lambda s: s.status is CaseStatus.EXTRACTED)
    await handle.signal(CaseWorkflow.confirm, Confirmation())
    return handle


async def test_approve_the_current_draft(env: WorkflowEnvironment) -> None:
    first = drafted()
    fakes = FakeActivities(draft=[first])
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        state = await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        assert state.draft_id == first.draft_id and state.drafts_used == 1

        # A decision about some other draft is refused; a redraft signal is ignored here.
        await handle.signal(CaseWorkflow.redraft)
        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=uuid.uuid4()))
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert state.last_error == "That draft is no longer the current one."

        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=first.draft_id))
        state = await wait_for(handle, lambda s: s.approved_draft_id is not None)
        assert state.approved_draft_id == first.draft_id
        assert state.last_error is None
        assert state.status is CaseStatus.AWAITING_APPROVAL  # until sending exists

        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
        history = await handle.fetch_history()

    assert fakes.names() == [RUN_INTAKE, CONFIRM, ASSESS, DRAFT, APPROVE_DRAFT, MARK_ABANDONED]
    approve_arg: DraftDecisionInput = fakes.calls[4][1]
    assert approve_arg.draft_id == first.draft_id
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_reject_drafts_again(env: WorkflowEnvironment) -> None:
    first, second = drafted(), drafted()
    fakes = FakeActivities(draft=[first, second])
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        await wait_for(handle, lambda s: s.draft_id == first.draft_id)
        await handle.signal(CaseWorkflow.reject_draft, DraftDecision(draft_id=first.draft_id))
        state = await wait_for(handle, lambda s: s.draft_id == second.draft_id)
        assert state.status is CaseStatus.AWAITING_APPROVAL and state.drafts_used == 2

        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=second.draft_id))
        await wait_for(handle, lambda s: s.approved_draft_id == second.draft_id)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()

    assert fakes.names() == [
        RUN_INTAKE, CONFIRM, ASSESS, DRAFT, REJECT_DRAFT, DRAFT, APPROVE_DRAFT, MARK_ABANDONED
    ]  # fmt: skip
    seqs = [arg.seq for name, arg in fakes.calls if name == DRAFT]
    assert seqs == [1, 2]


async def test_failed_draft_waits_for_redraft(env: WorkflowEnvironment) -> None:
    ok = drafted()
    fakes = FakeActivities(draft=[drafted(DraftStatus.FAILED), ok])
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        state = await wait_for(handle, lambda s: s.can_redraft)
        assert state.status is CaseStatus.ASSESSED
        assert state.draft_id is None
        assert "could not be produced" in (state.last_error or "")

        await handle.signal(CaseWorkflow.redraft)
        state = await wait_for(handle, lambda s: s.draft_id == ok.draft_id)
        assert state.status is CaseStatus.AWAITING_APPROVAL
        assert not state.can_redraft and state.last_error is None
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert [a.seq for n, a in fakes.calls if n == DRAFT] == [1, 2]


async def test_draft_activity_error_also_allows_redraft(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(draft=[bad_input("This case cannot be drafted: x"), drafted()])
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        state = await wait_for(handle, lambda s: s.can_redraft)
        assert state.last_error == "Drafting failed: This case cannot be drafted: x"
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names()[-1] == MARK_ABANDONED


async def test_refused_approval_keeps_waiting(env: WorkflowEnvironment) -> None:
    only = drafted()
    fakes = FakeActivities(
        draft=[only], approve=[bad_input("The letter cannot be approved: bad amount"), None]
    )
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        await wait_for(handle, lambda s: s.draft_id == only.draft_id)
        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=only.draft_id))
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert "bad amount" in (state.last_error or "")
        assert state.approved_draft_id is None

        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=only.draft_id))
        await wait_for(handle, lambda s: s.approved_draft_id == only.draft_id)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names().count(APPROVE_DRAFT) == 2


async def test_not_covered_case_is_not_drafted(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(assess=[AssessSummary(covered=False, total_owed_inr=Decimal("0"))])
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        await wait_for(handle, lambda s: s.status is CaseStatus.ASSESSED)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert DRAFT not in fakes.names()


async def test_draft_limit(env: WorkflowEnvironment) -> None:
    drafts = [drafted() for _ in range(MAX_DRAFTS_PER_CASE)]
    fakes = FakeActivities(draft=list(drafts), reject=[None] * MAX_DRAFTS_PER_CASE)
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        for d in drafts:
            await wait_for(handle, lambda s, d=d: s.draft_id == d.draft_id)  # type: ignore[misc]
            await handle.signal(CaseWorkflow.reject_draft, DraftDecision(draft_id=d.draft_id))
        state = await wait_for(handle, lambda s: "limit" in (s.last_error or ""))
        assert state.drafts_used == MAX_DRAFTS_PER_CASE
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names().count(DRAFT) == MAX_DRAFTS_PER_CASE


async def test_abandon_while_awaiting_approval(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest(reason="bank already paid"))
        outcome: CaseOutcome = await handle.result()
    assert outcome.reason == "bank already paid"
    assert fakes.names()[-2:] == [DRAFT, MARK_ABANDONED]
