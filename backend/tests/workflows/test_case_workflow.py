"""CaseWorkflow on Temporal's time-skipping test server, with fake activities (no LLM, no DB)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
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

from recourse.agents.draft_graph import DraftKind, DraftStatus
from recourse.agents.intake_graph import IntakeStatus
from recourse.agents.reply_judge_graph import NextAction, ReplyLabel
from recourse.cases.assessment import CaseDetails
from recourse.cases.drafts import MAX_DRAFTS_PER_CASE
from recourse.db.models import Actor, CaseStatus, OutboundStatus
from recourse.email.channel import ChannelKind
from recourse.rules.models import BankType
from recourse.temporal.case_types import (
    APPROVE_DRAFT,
    ASSESS,
    ASSESS_ESCALATION,
    CONFIRM,
    DRAFT,
    INVALID_USER_INPUT,
    JUDGE_REPLY,
    MARK_ABANDONED,
    PREPARE_SEND,
    RECORD_CLOSED,
    RECORD_FILED,
    RECORD_RESOLVED,
    RECORD_SENT,
    REJECT_DRAFT,
    RUN_INTAKE,
    AbandonRequest,
    AssessInput,
    AssessSummary,
    BankReplied,
    CaseInput,
    CaseOutcome,
    CaseState,
    CloseOutcome,
    CloseRequest,
    Confirmation,
    ConfirmInput,
    ConfirmOutcome,
    DraftDecision,
    DraftDecisionInput,
    DraftInput,
    DraftSummary,
    EscalationInput,
    EscalationSummary,
    IntakeSummary,
    JudgeReplyInput,
    MarkAbandonedInput,
    MarkFiled,
    MarkResolved,
    MarkSent,
    PrepareSendInput,
    RecordClosedInput,
    RecordFiledInput,
    RecordResolvedInput,
    RecordSentInput,
    ReplySummary,
    RunIntakeInput,
    SendSummary,
    TimeScaleInput,
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
    prepare: list[Any] = field(default_factory=lambda: [prepared()])
    record: list[Any] = field(default_factory=lambda: [None])
    escalation: list[Any] = field(default_factory=list)  # empty → `waiting()`
    judge: list[Any] = field(default_factory=list)
    resolved: list[Any] = field(default_factory=lambda: [None])
    filed: list[Any] = field(default_factory=lambda: [None])
    closed: list[Any] = field(default_factory=lambda: [None])
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

        @activity.defn(name=PREPARE_SEND)
        async def prepare_send(inp: PrepareSendInput) -> SendSummary:
            result: SendSummary = self._next(PREPARE_SEND, self.prepare, inp)
            return result

        @activity.defn(name=RECORD_SENT)
        async def record_sent(inp: RecordSentInput) -> None:
            self._next(RECORD_SENT, self.record, inp)

        @activity.defn(name=ASSESS_ESCALATION)
        async def assess_escalation(inp: EscalationInput) -> EscalationSummary:
            if not self.escalation:
                self.calls.append((ASSESS_ESCALATION, inp))
                return waiting()
            result: EscalationSummary = self._next(ASSESS_ESCALATION, self.escalation, inp)
            return result

        @activity.defn(name=JUDGE_REPLY)
        async def judge_reply(inp: JudgeReplyInput) -> ReplySummary:
            result: ReplySummary = self._next(JUDGE_REPLY, self.judge, inp)
            return result

        @activity.defn(name=RECORD_RESOLVED)
        async def record_resolved(inp: RecordResolvedInput) -> None:
            self._next(RECORD_RESOLVED, self.resolved, inp)

        @activity.defn(name=RECORD_FILED)
        async def record_filed(inp: RecordFiledInput) -> None:
            self._next(RECORD_FILED, self.filed, inp)

        @activity.defn(name=RECORD_CLOSED)
        async def record_closed(inp: RecordClosedInput) -> None:
            self._next(RECORD_CLOSED, self.closed, inp)

        @activity.defn(name=MARK_ABANDONED)
        async def mark_abandoned(inp: MarkAbandonedInput) -> None:
            self.calls.append((MARK_ABANDONED, inp))

        return [
            run_intake,
            confirm,
            assess,
            draft,
            approve_draft,
            reject_draft,
            prepare_send,
            record_sent,
            assess_escalation,
            judge_reply,
            record_resolved,
            record_filed,
            record_closed,
            mark_abandoned,
        ]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def drafted(status: DraftStatus = DraftStatus.DRAFTED) -> DraftSummary:
    return DraftSummary(draft_id=uuid.uuid4(), status=status, rules_verified=False)


def prepared(status: OutboundStatus = OutboundStatus.PREPARED, *reasons: str) -> SendSummary:
    return SendSummary(
        email_id=uuid.uuid4(),
        channel=ChannelKind.MANUAL,
        status=status,
        sandbox=True,
        blocked_reasons=list(reasons),
    )


def waiting(wait_days: int = 30) -> EscalationSummary:
    return EscalationSummary(
        entity_coverage="COVERED",
        can_file_now=wait_days == 0,
        eligible_from=date(2026, 12, 20),
        filing_deadline=date(2027, 3, 19),
        wait_days=wait_days,
    )


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


async def to_approval(
    env: WorkflowEnvironment, fakes: FakeActivities, inp: CaseInput | None = None
) -> WorkflowHandle[Any, Any]:
    handle = await start(env, inp or case_input())
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

    assert fakes.names() == [
        RUN_INTAKE, CONFIRM, ASSESS, DRAFT, APPROVE_DRAFT, PREPARE_SEND, MARK_ABANDONED
    ]  # fmt: skip
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
        RUN_INTAKE, CONFIRM, ASSESS, DRAFT, REJECT_DRAFT, DRAFT, APPROVE_DRAFT, PREPARE_SEND,
        MARK_ABANDONED,
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


# =================================================================================================
# Sending (ManualChannel)
# =================================================================================================

SENT_AT = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


async def to_approved(env: WorkflowEnvironment, fakes: FakeActivities) -> WorkflowHandle[Any, Any]:
    handle = await to_approval(env, fakes)
    state = await wait_for(handle, lambda s: s.draft_id is not None)
    assert state.draft_id is not None
    await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=state.draft_id))
    return handle


async def test_manual_send_to_sent_to_bank(env: WorkflowEnvironment) -> None:
    email = prepared()
    fakes = FakeActivities(
        prepare=[email], record=[bad_input("The send time is in the future."), None]
    )
    async with worker(env, fakes):
        handle = await to_approved(env, fakes)
        state = await wait_for(handle, lambda s: s.awaiting_manual_send)
        assert state.email_id == email.email_id
        assert state.status is CaseStatus.AWAITING_APPROVAL

        await handle.signal(CaseWorkflow.mark_sent, MarkSent(sent_at=SENT_AT))
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert state.last_error == "The send time is in the future."
        assert state.awaiting_manual_send

        await handle.signal(CaseWorkflow.mark_sent, MarkSent(sent_at=SENT_AT))
        state = await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_BANK)
        assert not state.awaiting_manual_send and state.last_error is None

        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
        history = await handle.fetch_history()

    record_arg: RecordSentInput = [a for n, a in fakes.calls if n == RECORD_SENT][-1]
    assert record_arg.email_id == email.email_id and record_arg.mark.sent_at == SENT_AT
    prepare_arg: PrepareSendInput = next(a for n, a in fakes.calls if n == PREPARE_SEND)
    assert prepare_arg.draft_id == next(a for n, a in fakes.calls if n == APPROVE_DRAFT).draft_id
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_blocked_send_waits_for_retry(env: WorkflowEnvironment) -> None:
    ok = prepared()
    fakes = FakeActivities(
        prepare=[prepared(OutboundStatus.BLOCKED, "The bank is not known; choose your bank."), ok]
    )
    async with worker(env, fakes):
        handle = await to_approved(env, fakes)
        state = await wait_for(handle, lambda s: s.can_retry_send)
        assert "choose your bank" in (state.last_error or "")
        assert not state.awaiting_manual_send

        await handle.signal(CaseWorkflow.retry_send)
        state = await wait_for(handle, lambda s: s.awaiting_manual_send)
        assert state.email_id == ok.email_id and not state.can_retry_send
        assert state.last_error is None
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert fakes.names().count(PREPARE_SEND) == 2


async def test_a_channel_that_sends_itself_skips_mark_sent(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(prepare=[prepared(OutboundStatus.SENT)])
    async with worker(env, fakes):
        handle = await to_approved(env, fakes)
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_BANK)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert RECORD_SENT not in fakes.names()


async def test_mark_sent_before_the_email_exists_is_ignored(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        state = await wait_for(handle, lambda s: s.draft_id is not None)
        await handle.signal(CaseWorkflow.mark_sent, MarkSent(sent_at=SENT_AT))  # too early
        assert state.draft_id is not None
        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=state.draft_id))
        await wait_for(handle, lambda s: s.awaiting_manual_send)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert RECORD_SENT not in fakes.names()


async def test_abandon_while_waiting_for_the_user_to_send(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_approved(env, fakes)
        await wait_for(handle, lambda s: s.awaiting_manual_send)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        outcome: CaseOutcome = await handle.result()
    assert outcome.status is CaseStatus.ABANDONED
    assert fakes.names()[-2:] == [PREPARE_SEND, MARK_ABANDONED]


# =================================================================================================
# The bank's reply window
# =================================================================================================


async def to_awaiting_bank(
    env: WorkflowEnvironment, fakes: FakeActivities, inp: CaseInput | None = None
) -> WorkflowHandle[Any, Any]:
    handle = await to_approval(env, fakes, inp)
    state = await wait_for(handle, lambda s: s.draft_id is not None)
    assert state.draft_id is not None
    await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=state.draft_id))
    await wait_for(handle, lambda s: s.awaiting_manual_send)
    await handle.signal(CaseWorkflow.mark_sent, MarkSent(sent_at=SENT_AT))
    await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_BANK)
    return handle


async def test_bank_wait_timer_expires(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        state: CaseState = await handle.query(CaseWorkflow.get_state)
        assert state.escalation_eligible_from == date(2026, 12, 20)
        assert state.filing_deadline == date(2027, 3, 19)
        assert not state.bank_wait_expired

        await env.sleep(timedelta(days=29))
        state = await handle.query(CaseWorkflow.get_state)
        assert not state.bank_wait_expired

        await env.sleep(timedelta(days=2))
        state = await wait_for(handle, lambda s: s.bank_wait_expired)
        assert state.status is CaseStatus.AWAITING_BANK
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
        history = await handle.fetch_history()

    escalations: list[EscalationInput] = [a for n, a in fakes.calls if n == ASSESS_ESCALATION]
    assert [e.event for e in escalations][:2] == ["bank.wait_started", "bank.wait_expired"]
    assert escalations[0].new_status is CaseStatus.AWAITING_BANK
    assert escalations[1].new_status is None
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_demo_mode_shrinks_the_bank_wait(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    inp = CaseInput(
        case_id=uuid.uuid4(),
        upload_id=uuid.uuid4(),
        time_scale=TimeScaleInput(demo_mode=True, demo_day_seconds=60),
    )
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes, inp)
        await env.sleep(timedelta(minutes=29))
        state: CaseState = await handle.query(CaseWorkflow.get_state)
        assert not state.bank_wait_expired
        await env.sleep(timedelta(minutes=2))  # 30 "days" of 60 seconds
        await wait_for(handle, lambda s: s.bank_wait_expired)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
        history = await handle.fetch_history()

    # The case's calendar moved on by the 30 skipped days, though only minutes passed, so the
    # rule engine sees the waiting period as over (regression: it looped forever).
    started, expired = [a for n, a in fakes.calls if n == ASSESS_ESCALATION][:2]
    assert expired.event == "bank.wait_expired"
    assert (expired.as_of - started.as_of).days == 30
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_window_already_over_expires_at_once(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(escalation=[waiting(0), waiting(0)])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await wait_for(handle, lambda s: s.bank_wait_expired)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()


async def test_abandon_while_awaiting_bank(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest(reason="bank paid"))
        outcome: CaseOutcome = await handle.result()
    assert outcome.reason == "bank paid"
    assert fakes.names()[-2:] == [ASSESS_ESCALATION, MARK_ABANDONED]


async def test_escalation_error_keeps_the_case_open(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(
        escalation=[bad_input("The complaint has not been sent to the bank yet.")]
    )
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert "has not been sent" in (state.last_error or "")
        await env.sleep(timedelta(days=60))
        state = await handle.query(CaseWorkflow.get_state)
        assert not state.bank_wait_expired
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()


# =================================================================================================
# Bank replies
# =================================================================================================


def verdict(action: NextAction, label: ReplyLabel = ReplyLabel.UNCLEAR) -> ReplySummary:
    return ReplySummary(
        reply_id=uuid.uuid4(),
        label=label,
        next_action=action,
        suspicious=action is NextAction.WARN,
        warnings=["This looks like a scam."] if action is NextAction.WARN else [],
    )


def events(fakes: FakeActivities) -> list[str]:
    return [a.event for n, a in fakes.calls if n == ASSESS_ESCALATION]


async def reply(handle: WorkflowHandle[Any, Any]) -> None:
    await handle.signal(CaseWorkflow.bank_replied, BankReplied(reply_id=uuid.uuid4()))


async def test_acknowledgement_keeps_the_timer_running(env: WorkflowEnvironment) -> None:
    ack = verdict(NextAction.KEEP_WAITING, ReplyLabel.ACKNOWLEDGEMENT)
    fakes = FakeActivities(judge=[ack])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await env.sleep(timedelta(days=10))
        await reply(handle)
        state = await wait_for(handle, lambda s: s.last_reply is not None)
        assert state.last_reply == ack and state.status is CaseStatus.AWAITING_BANK
        await env.sleep(timedelta(days=21))  # 31 days after sending
        await wait_for(handle, lambda s: s.bank_wait_expired)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
        history = await handle.fetch_history()
    assert events(fakes)[:3] == ["bank.wait_started", "bank.replied", "bank.wait_expired"]
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_phishing_reply_only_warns_then_user_resolves(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(judge=[verdict(NextAction.WARN)])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await reply(handle)
        state = await wait_for(handle, lambda s: s.last_reply is not None)
        assert state.last_reply is not None and state.last_reply.warnings
        assert state.status is CaseStatus.AWAITING_BANK and not state.can_escalate

        await handle.signal(CaseWorkflow.mark_resolved, MarkResolved(amount_received_inr=2750))
        outcome: CaseOutcome = await handle.result()
    assert outcome.status is CaseStatus.RESOLVED
    # A suspicious reply is not a bank communication: no re-assessment for it.
    assert events(fakes) == ["bank.wait_started"]
    resolved: RecordResolvedInput = next(a for n, a in fakes.calls if n == RECORD_RESOLVED)
    assert resolved.mark.amount_received_inr == 2750
    assert MARK_ABANDONED not in fakes.names()


async def test_non_compliant_reply_escalates(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(judge=[verdict(NextAction.ESCALATE, ReplyLabel.PARTIAL)])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await reply(handle)
        state = await wait_for(handle, lambda s: s.status is CaseStatus.JUDGED)
        assert not state.can_escalate  # already escalating, no question for the user
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert events(fakes)[:2] == ["bank.wait_started", "bank.replied"]


async def test_rejection_with_reasons_waits_for_the_user(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(judge=[verdict(NextAction.ASK_USER, ReplyLabel.REJECTED_WITH_REASONS)])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await reply(handle)
        state = await wait_for(handle, lambda s: s.can_escalate)
        assert state.status is CaseStatus.JUDGED
        await env.sleep(timedelta(days=60))  # the timer is paused while the user decides
        state = await handle.query(CaseWorkflow.get_state)
        assert not state.bank_wait_expired and state.can_escalate

        await handle.signal(CaseWorkflow.escalate)
        await wait_for(handle, lambda s: not s.can_escalate)
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert events(fakes)[:3] == ["bank.wait_started", "bank.replied", "case.escalation_chosen"]


async def test_escalate_without_a_question_is_ignored(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await handle.signal(CaseWorkflow.escalate)
        await env.sleep(timedelta(days=5))
        state: CaseState = await handle.query(CaseWorkflow.get_state)
        assert state.status is CaseStatus.AWAITING_BANK
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert events(fakes) == ["bank.wait_started"]


async def test_reply_before_sending_is_ignored(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities()
    async with worker(env, fakes):
        handle = await to_approval(env, fakes)
        await wait_for(handle, lambda s: s.status is CaseStatus.AWAITING_APPROVAL)
        await reply(handle)
        await handle.signal(CaseWorkflow.mark_resolved, MarkResolved(amount_received_inr=1))
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert JUDGE_REPLY not in fakes.names() and RECORD_RESOLVED not in fakes.names()


async def test_judging_error_keeps_waiting(env: WorkflowEnvironment) -> None:
    ack = verdict(NextAction.KEEP_WAITING, ReplyLabel.ACKNOWLEDGEMENT)
    fakes = FakeActivities(
        judge=[ApplicationError("model refused", type="LLMFailed", non_retryable=True), ack]
    )
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await reply(handle)
        state = await wait_for(handle, lambda s: s.last_error is not None)
        assert "could not be checked" in (state.last_error or "")
        await reply(handle)
        state = await wait_for(handle, lambda s: s.last_reply is not None)
        assert state.last_error is None
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()


async def test_refused_resolution_keeps_waiting(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(resolved=[bad_input("not now"), None])
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await handle.signal(CaseWorkflow.mark_resolved, MarkResolved(amount_received_inr=10))
        state = await wait_for(handle, lambda s: s.last_error == "not now")
        assert state.status is CaseStatus.AWAITING_BANK
        await handle.signal(CaseWorkflow.mark_resolved, MarkResolved(amount_received_inr=10))
        outcome: CaseOutcome = await handle.result()
    assert outcome.status is CaseStatus.RESOLVED


# =================================================================================================
# Escalation to the RBI Ombudsman
# =================================================================================================

FILED_AT = datetime(2026, 12, 21, 10, 0, tzinfo=UTC)


def needs_type() -> EscalationSummary:
    return EscalationSummary(
        entity_coverage="UNKNOWN",
        can_file_now=False,
        eligible_from=date(2026, 12, 20),
        filing_deadline=date(2027, 3, 19),
        wait_days=0,
        needs_bank_type=True,
    )


async def to_escalation_draft(
    env: WorkflowEnvironment, fakes: FakeActivities
) -> WorkflowHandle[Any, Any]:
    """Send the complaint, let the bank window run out, reach the Ombudsman draft."""
    handle = await to_awaiting_bank(env, fakes)
    await env.sleep(timedelta(days=31))
    await wait_for(handle, lambda s: s.status is CaseStatus.ESCALATION_DRAFT_READY)
    return handle


async def test_escalation_to_closed(env: WorkflowEnvironment) -> None:
    letter = drafted()
    fakes = FakeActivities(
        escalation=[waiting(30), waiting(0), waiting(0)],
        draft=[drafted(), letter],
        approve=[None, None],
    )
    async with worker(env, fakes):
        handle = await to_escalation_draft(env, fakes)
        state: CaseState = await handle.query(CaseWorkflow.get_state)
        assert state.draft_id == letter.draft_id and state.bank_wait_expired

        await handle.signal(CaseWorkflow.mark_filed, MarkFiled(filed_at=FILED_AT))  # too early
        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=letter.draft_id))
        state = await wait_for(handle, lambda s: s.awaiting_filing)
        assert state.escalation_draft_id == letter.draft_id

        await handle.signal(
            CaseWorkflow.mark_filed, MarkFiled(filed_at=FILED_AT, reference="N-202612-0042")
        )
        state = await wait_for(handle, lambda s: s.can_close)
        assert state.status is CaseStatus.FILED_WITH_OMBUDSMAN and not state.awaiting_filing

        await handle.signal(
            CaseWorkflow.close,
            CloseRequest(outcome=CloseOutcome.AWARD_RECEIVED, amount_received_inr=2750),
        )
        outcome: CaseOutcome = await handle.result()
        history = await handle.fetch_history()

    assert outcome == CaseOutcome(status=CaseStatus.CLOSED, reason="AWARD_RECEIVED")
    drafts: list[DraftInput] = [a for n, a in fakes.calls if n == DRAFT]
    assert [d.kind for d in drafts] == [DraftKind.BANK_COMPLAINT, DraftKind.OMBUDSMAN_COMPLAINT]
    assert [d.seq for d in drafts] == [1, 2]
    assert events(fakes) == ["bank.wait_started", "bank.wait_expired", "escalation.checked"]
    filed: RecordFiledInput = next(a for n, a in fakes.calls if n == RECORD_FILED)
    assert filed.draft_id == letter.draft_id and filed.mark.reference == "N-202612-0042"
    assert fakes.names().count(RECORD_FILED) == 1  # the early mark was ignored
    assert MARK_ABANDONED not in fakes.names()
    await Replayer(
        workflows=[CaseWorkflow], data_converter=pydantic_data_converter
    ).replay_workflow(history)


async def test_escalation_waits_for_the_bank_type(env: WorkflowEnvironment) -> None:
    fakes = FakeActivities(
        escalation=[waiting(30), waiting(0), needs_type(), waiting(0)],
        draft=[drafted(), drafted()],
    )
    async with worker(env, fakes):
        handle = await to_awaiting_bank(env, fakes)
        await env.sleep(timedelta(days=31))
        state = await wait_for(handle, lambda s: s.needs_bank_type)
        assert state.can_recheck_escalation and "kind of bank" in (state.last_error or "")
        await handle.signal(CaseWorkflow.recheck_escalation)
        state = await wait_for(handle, lambda s: s.status is CaseStatus.ESCALATION_DRAFT_READY)
        assert not state.needs_bank_type and state.last_error is None
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        await handle.result()
    assert events(fakes)[-2:] == ["escalation.checked", "escalation.checked"]


async def test_filing_deadline_reminder(env: WorkflowEnvironment) -> None:
    letter = drafted()
    fakes = FakeActivities(
        escalation=[waiting(30), waiting(0), waiting(0)],
        draft=[drafted(), letter],
        approve=[None, None],
    )
    async with worker(env, fakes):
        handle = await to_escalation_draft(env, fakes)
        await handle.signal(CaseWorkflow.approve_draft, DraftDecision(draft_id=letter.draft_id))
        await wait_for(handle, lambda s: s.awaiting_filing)
        await env.sleep(timedelta(days=200))  # past 19 Mar 2027 whatever today is
        state = await wait_for(handle, lambda s: s.filing_deadline_passed)
        assert state.awaiting_filing
        await handle.signal(CaseWorkflow.abandon, AbandonRequest())
        outcome: CaseOutcome = await handle.result()
    assert outcome.status is CaseStatus.ABANDONED
