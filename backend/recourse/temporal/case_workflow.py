"""CaseWorkflow: one per case, runs for weeks. Orchestration only (CLAUDE.md §3).

    run_intake ──(not payment evidence)──────────────────────────────→ ABANDONED
        │
        ▼
    EXTRACTED: wait for `confirm` signals; each one is applied by the confirm activity
        │      (still issues → keep waiting, with the issues in `get_state`)
        ▼
    assess (rule engine) → ASSESSED ──(not covered)──→ wait (nothing to draft)
        │
        ▼
    draft (DraftGraph) ──(failed closed)──→ ASSESSED: wait for `redraft`
        │
        ▼
    AWAITING_APPROVAL ──reject_draft──→ draft again (at most MAX_DRAFTS_PER_CASE drafts)
        │ approve_draft
        ▼
    prepare_send (send policy + channel) ──(blocked)──→ wait for `retry_send`
        │ ManualChannel: PREPARED, the user sends it themselves
        ▼
    wait for `mark_sent` → record_sent → SENT_TO_BANK
        │
        ▼
    assess_escalation (rule engine) → AWAITING_BANK: timer until the Ombudsman waiting period
        │                              ends (30 days, or the bank's longer timeline for fraud)
        │  bank_replied → judge_reply (ReplyJudgeGraph) → next action:
        │     WARN / KEEP_WAITING / USER_ACTION → keep waiting (the timer keeps running)
        │     PROPOSE_RESOLVED / ASK_USER → JUDGED: the user decides (`mark_resolved`,
        │                                   `escalate` or `abandon`); no timer
        │     ESCALATE → escalation
        │  mark_resolved (any time) → record_resolved → RESOLVED (the workflow ends)
        ▼ timer fires (bank_wait_expired) or escalation chosen
    escalation → wait (the Ombudsman draft is the next step)

An `abandon` signal ends the case from any waiting point. Every status change is written to the
database by the activity that causes it (with an audit event), so the API reads the database
and does not depend on Temporal being up.

Determinism rules: no I/O, clock or randomness here. Time comes from `workflow.now()`.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from recourse.agents.draft_graph import DraftStatus
    from recourse.agents.intake_graph import IntakeStatus
    from recourse.agents.reply_judge_graph import NextAction
    from recourse.cases.drafts import MAX_DRAFTS_PER_CASE
    from recourse.db.models import Actor, CaseStatus, OutboundStatus
    from recourse.evidence.models import IST
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
        MarkResolved,
        MarkSent,
        PrepareSendInput,
        RecordResolvedInput,
        RecordSentInput,
        ReplySummary,
        RunIntakeInput,
        SendSummary,
    )
    from recourse.temporal.timeutil import TimeScale, days

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


def _scale(inp: CaseInput) -> TimeScale:
    return TimeScale(
        demo_mode=inp.time_scale.demo_mode, demo_day_seconds=inp.time_scale.demo_day_seconds
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
        # Drafting
        self._decisions: list[tuple[str, DraftDecision]] = []  # ("approve" | "reject", ...)
        self._redraft = False
        self._can_redraft = False
        self._draft_id: uuid.UUID | None = None
        self._approved_draft_id: uuid.UUID | None = None
        self._drafts_used = 0
        # Sending
        self._email_id: uuid.UUID | None = None
        self._marks: list[MarkSent] = []
        self._awaiting_manual_send = False
        self._retry_send = False
        self._can_retry_send = False
        # The bank's reply window
        self._eligible_from: date | None = None
        self._filing_deadline: date | None = None
        self._bank_wait_expired = False
        self._replies: list[BankReplied] = []
        self._last_reply: ReplySummary | None = None
        self._resolved: list[MarkResolved] = []
        self._escalate = False
        self._can_escalate = False
        self._awaiting_bank = False  # bank_replied / mark_resolved are accepted

    # --- signals & queries ----------------------------------------------------------------------

    @workflow.signal(name="confirm")
    def confirm(self, confirmation: Confirmation) -> None:
        self._confirmations.append(confirmation)

    @workflow.signal(name="approve_draft")
    def approve_draft(self, decision: DraftDecision) -> None:
        self._decisions.append(("approve", decision))

    @workflow.signal(name="reject_draft")
    def reject_draft(self, decision: DraftDecision) -> None:
        self._decisions.append(("reject", decision))

    @workflow.signal(name="redraft")
    def redraft(self) -> None:
        if self._can_redraft:
            self._redraft = True

    @workflow.signal(name="mark_sent")
    def mark_sent(self, mark: MarkSent) -> None:
        self._marks.append(mark)

    @workflow.signal(name="retry_send")
    def retry_send(self) -> None:
        if self._can_retry_send:
            self._retry_send = True

    @workflow.signal(name="bank_replied")
    def bank_replied(self, reply: BankReplied) -> None:
        if self._awaiting_bank:
            self._replies.append(reply)

    @workflow.signal(name="mark_resolved")
    def mark_resolved(self, mark: MarkResolved) -> None:
        if self._awaiting_bank:
            self._resolved.append(mark)

    @workflow.signal(name="escalate")
    def escalate(self) -> None:
        if self._can_escalate:
            self._escalate = True

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
            draft_id=self._draft_id,
            approved_draft_id=self._approved_draft_id,
            drafts_used=self._drafts_used,
            can_redraft=self._can_redraft,
            email_id=self._email_id,
            awaiting_manual_send=self._awaiting_manual_send,
            can_retry_send=self._can_retry_send,
            escalation_eligible_from=self._eligible_from,
            filing_deadline=self._filing_deadline,
            bank_wait_expired=self._bank_wait_expired,
            last_reply=self._last_reply,
            can_escalate=self._can_escalate,
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
                assessed: AssessSummary = await workflow.execute_activity(
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

        # Cases that reached ASSESSED before drafting existed keep their old path on replay.
        if workflow.patched("drafting-v1") and assessed.covered:
            # 4. Draft the bank complaint and get the user's approval.
            if not await self._draft_until_approved(inp):
                assert self._abandon is not None
                return await self._end(inp, self._abandon.reason, Actor.USER)
            # 5. Send the approved letter (or hand it to the user to send).
            if workflow.patched("sending-v1"):
                if not await self._send_approved(inp):
                    assert self._abandon is not None
                    return await self._end(inp, self._abandon.reason, Actor.USER)
                self._status = CaseStatus.SENT_TO_BANK
                # 6. Wait for the bank's reply, within the window the rules give it.
                if workflow.patched("bank-wait-v1"):
                    result = await self._wait_for_bank(inp)
                    if result == "abandoned":
                        assert self._abandon is not None
                        return await self._end(inp, self._abandon.reason, Actor.USER)
                    if result == "resolved":
                        return CaseOutcome(status=CaseStatus.RESOLVED)

        # 7. Next milestone (escalation) plugs in here. Until then: wait.
        await workflow.wait_condition(lambda: self._abandoned)
        assert self._abandon is not None
        return await self._end(inp, self._abandon.reason, Actor.USER)

    # --- drafting -------------------------------------------------------------------------------

    async def _draft_until_approved(self, inp: CaseInput) -> bool:
        """Draft, then wait for the user's decision; redraft on rejection. False = abandoned
        (or the draft limit was reached and the case was abandoned while waiting)."""
        while True:
            if self._drafts_used >= MAX_DRAFTS_PER_CASE:
                self._last_error = (
                    f"The limit of {MAX_DRAFTS_PER_CASE} drafts for this case was reached."
                )
                await workflow.wait_condition(lambda: self._abandoned)
                return False
            self._drafts_used += 1
            summary = await self._run_draft(inp, self._drafts_used)
            if summary is None or summary.status is DraftStatus.FAILED:
                # Fail closed: no letter. The user may ask for another try.
                if summary is not None:
                    self._last_error = "A checked draft could not be produced. Please try again."
                self._status = CaseStatus.ASSESSED
                self._can_redraft = True
                await workflow.wait_condition(lambda: self._redraft or self._abandoned)
                self._can_redraft = self._redraft = False
                if self._abandoned:
                    return False
                self._last_error = None
                continue

            self._status = CaseStatus.AWAITING_APPROVAL
            self._draft_id = summary.draft_id
            self._last_error = None
            outcome = await self._wait_for_decision(inp, summary.draft_id)
            if outcome == "abandoned":
                return False
            if outcome == "approved":
                self._approved_draft_id = summary.draft_id
                return True
            # rejected → draft again
            self._status = CaseStatus.ASSESSED
            self._draft_id = None

    # --- sending ----------------------------------------------------------------------------------

    async def _send_approved(self, inp: CaseInput) -> bool:
        """Prepare the approved draft for sending; for ManualChannel wait until the user says
        it was sent. False = abandoned."""
        assert self._approved_draft_id is not None
        while True:
            summary = await self._prepare_send(inp, self._approved_draft_id)
            if summary is not None:
                self._email_id = summary.email_id
                if summary.status is OutboundStatus.SENT:
                    return True
                if summary.status is OutboundStatus.PREPARED:
                    break
                self._last_error = " ".join(summary.blocked_reasons)
            # Blocked (or failed): wait until the user fixed the cause and asks to retry.
            self._can_retry_send = True
            await workflow.wait_condition(lambda: self._retry_send or self._abandoned)
            self._can_retry_send = self._retry_send = False
            if self._abandoned:
                return False
            self._last_error = None

        self._marks.clear()  # a mark sent before the email existed means nothing
        self._awaiting_manual_send = True
        self._last_error = None
        while True:
            await workflow.wait_condition(lambda: bool(self._marks) or self._abandoned)
            if self._abandoned:
                return False
            mark = self._marks.pop(0)
            assert self._email_id is not None
            try:
                await workflow.execute_activity(
                    RECORD_SENT,
                    RecordSentInput(
                        case_id=inp.case_id, email_id=self._email_id, mark=mark, now=workflow.now()
                    ),
                    start_to_close_timeout=DB_TIMEOUT,
                    retry_policy=DB_RETRY,
                )
            except ActivityError as err:
                self._last_error = _error_message(err)
                continue
            self._awaiting_manual_send = False
            self._last_error = None
            return True

    async def _prepare_send(self, inp: CaseInput, draft_id: uuid.UUID) -> SendSummary | None:
        try:
            summary: SendSummary = await workflow.execute_activity(
                PREPARE_SEND,
                PrepareSendInput(case_id=inp.case_id, draft_id=draft_id),
                result_type=SendSummary,
                start_to_close_timeout=DB_TIMEOUT,
                retry_policy=DB_RETRY,
            )
        except ActivityError as err:
            self._last_error = f"Sending failed: {_error_message(err)}"
            return None
        return summary

    async def _run_draft(self, inp: CaseInput, seq: int) -> DraftSummary | None:
        try:
            summary: DraftSummary = await workflow.execute_activity(
                DRAFT,
                DraftInput(
                    case_id=inp.case_id,
                    seq=seq,
                    letter_date=workflow.now().astimezone(IST).date(),
                ),
                result_type=DraftSummary,
                start_to_close_timeout=LLM_TIMEOUT,
                retry_policy=LLM_RETRY,
            )
        except ActivityError as err:
            self._last_error = f"Drafting failed: {_error_message(err)}"
            return None
        return summary

    async def _wait_for_decision(self, inp: CaseInput, draft_id: uuid.UUID) -> str:
        """Apply approve/reject signals for this draft in order. Returns the outcome."""
        while True:
            await workflow.wait_condition(lambda: bool(self._decisions) or self._abandoned)
            if self._abandoned:
                return "abandoned"
            action, decision = self._decisions.pop(0)
            if decision.draft_id != draft_id:
                self._last_error = "That draft is no longer the current one."
                continue
            try:
                await workflow.execute_activity(
                    APPROVE_DRAFT if action == "approve" else REJECT_DRAFT,
                    DraftDecisionInput(case_id=inp.case_id, draft_id=draft_id, now=workflow.now()),
                    start_to_close_timeout=DB_TIMEOUT,
                    retry_policy=DB_RETRY,
                )
            except ActivityError as err:
                self._last_error = _error_message(err)
                continue
            self._last_error = None
            return "approved" if action == "approve" else "rejected"

    # --- the bank's reply window -----------------------------------------------------------------

    async def _wait_for_bank(self, inp: CaseInput) -> str:
        """AWAITING_BANK until the Ombudsman waiting period ends, handling the bank's replies.

        Returns "abandoned", "resolved" or "escalate".
        """
        summary = await self._assess_escalation(inp, "bank.wait_started", CaseStatus.AWAITING_BANK)
        self._status = CaseStatus.AWAITING_BANK
        self._awaiting_bank = True
        # Without a window (activity error) there is no timer: replies are still handled.
        deadline = (
            workflow.now() + days(summary.wait_days, _scale(inp)) if summary is not None else None
        )
        judged = False  # JUDGED: the user decides, the timer is paused

        def woken() -> bool:
            return bool(self._replies or self._resolved) or self._escalate or self._abandoned

        while True:
            timeout = None if judged or deadline is None else deadline - workflow.now()
            if timeout is not None and timeout <= timedelta(0):
                self._bank_wait_expired = True
            else:
                try:
                    await workflow.wait_condition(woken, timeout=timeout)
                except TimeoutError:
                    self._bank_wait_expired = True

            if self._abandoned:
                return "abandoned"
            if self._resolved:
                if await self._record_resolved(inp, self._resolved.pop(0)):
                    self._awaiting_bank = self._can_escalate = False
                    self._status = CaseStatus.RESOLVED
                    return "resolved"
                continue
            if self._escalate:
                self._escalate = False
                await self._assess_escalation(inp, "case.escalation_chosen", None)
                break
            if self._bank_wait_expired:
                await self._assess_escalation(inp, "bank.wait_expired", None)
                break
            if not self._replies:
                continue

            reply = await self._judge(inp, self._replies.pop(0))
            if reply is None:
                continue
            self._last_reply = reply
            if not reply.suspicious:
                await self._assess_escalation(inp, "bank.replied", None)
            action = reply.next_action
            if action is NextAction.ESCALATE:
                self._status = CaseStatus.JUDGED
                break
            if action in (NextAction.PROPOSE_RESOLVED, NextAction.ASK_USER):
                self._status = CaseStatus.JUDGED
                self._can_escalate = judged = True
            # WARN / KEEP_WAITING / USER_ACTION: keep waiting with the timer.

        self._awaiting_bank = self._can_escalate = False
        return "escalate"

    async def _judge(self, inp: CaseInput, reply: BankReplied) -> ReplySummary | None:
        try:
            summary: ReplySummary = await workflow.execute_activity(
                JUDGE_REPLY,
                JudgeReplyInput(case_id=inp.case_id, reply_id=reply.reply_id),
                result_type=ReplySummary,
                start_to_close_timeout=LLM_TIMEOUT,
                retry_policy=LLM_RETRY,
            )
        except ActivityError as err:
            self._last_error = f"The bank's reply could not be checked: {_error_message(err)}"
            return None
        self._last_error = None
        return summary

    async def _record_resolved(self, inp: CaseInput, mark: MarkResolved) -> bool:
        try:
            await workflow.execute_activity(
                RECORD_RESOLVED,
                RecordResolvedInput(case_id=inp.case_id, mark=mark, now=workflow.now()),
                start_to_close_timeout=DB_TIMEOUT,
                retry_policy=DB_RETRY,
            )
        except ActivityError as err:
            self._last_error = _error_message(err)
            return False
        self._last_error = None
        return True

    async def _assess_escalation(
        self, inp: CaseInput, event: str, new_status: CaseStatus | None
    ) -> EscalationSummary | None:
        try:
            summary: EscalationSummary = await workflow.execute_activity(
                ASSESS_ESCALATION,
                EscalationInput(
                    case_id=inp.case_id,
                    as_of=workflow.now().astimezone(IST).date(),
                    event=event,
                    new_status=new_status,
                ),
                result_type=EscalationSummary,
                start_to_close_timeout=DB_TIMEOUT,
                retry_policy=DB_RETRY,
            )
        except ActivityError as err:
            self._last_error = (
                f"The bank's reply window could not be worked out: {_error_message(err)}"
            )
            return None
        self._eligible_from = summary.eligible_from
        self._filing_deadline = summary.filing_deadline
        return summary

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
