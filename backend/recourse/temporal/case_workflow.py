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

import asyncio
import uuid
from datetime import date, datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from recourse.agents.draft_graph import DraftKind, DraftStatus
    from recourse.agents.intake_graph import IntakeStatus
    from recourse.agents.reply_judge_graph import NextAction
    from recourse.cases.drafts import MAX_DRAFTS_PER_CASE
    from recourse.db.models import Actor, CaseStatus, OutboundStatus
    from recourse.email.channel import ChannelKind
    from recourse.evidence.models import IST
    from recourse.temporal.case_types import (
        APPROVE_DRAFT,
        ASSESS,
        ASSESS_ESCALATION,
        CHECK_THREAD,
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
        SEND_EMAIL,
        AbandonRequest,
        AssessInput,
        AssessSummary,
        BankReplied,
        CaseInput,
        CaseOutcome,
        CaseState,
        CheckThreadInput,
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
        ResumeAtBank,
        RunIntakeInput,
        SendEmailInput,
        SendSummary,
        ThreadSummary,
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
# Gmail steps (network): one send attempt is never retried blindly (see send_email).
GMAIL_TIMEOUT = timedelta(seconds=90)
GMAIL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=3,
    non_retryable_error_types=[INVALID_USER_INPUT],
)
# After the bank's window: check the thread less often (escalation can take months).
LATE_POLL = timedelta(hours=6)
MAX_LATE_CHECKS = 1000
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
        # Escalation to the RBI Ombudsman
        self._escalation_drafts = 0
        self._needs_bank_type = False
        self._can_recheck = False
        self._recheck = False
        self._escalation_draft_id: uuid.UUID | None = None
        self._awaiting_filing = False
        self._filings: list[MarkFiled] = []
        self._filing_deadline_passed = False
        self._can_close = False
        self._close: CloseRequest | None = None
        self._demo_offset_days = 0  # see `_today`
        # GmailChannel reply watcher
        self._watching = False
        self._last_thread_check: datetime | None = None
        self._via_gmail = False  # the letter went out through Gmail: its thread can be read
        self._late = False  # with the Ombudsman: late replies and "I got my money" still count

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
        if self._awaiting_bank or self._late:
            self._replies.append(reply)

    @workflow.signal(name="mark_resolved")
    def mark_resolved(self, mark: MarkResolved) -> None:
        if self._awaiting_bank or self._late:
            self._resolved.append(mark)

    @workflow.signal(name="escalate")
    def escalate(self) -> None:
        if self._can_escalate:
            self._escalate = True

    @workflow.signal(name="recheck_escalation")
    def recheck_escalation(self) -> None:
        if self._can_recheck:
            self._recheck = True

    @workflow.signal(name="mark_filed")
    def mark_filed(self, mark: MarkFiled) -> None:
        if self._awaiting_filing:
            self._filings.append(mark)

    @workflow.signal(name="close")
    def close(self, request: CloseRequest) -> None:
        if self._can_close and self._close is None:
            self._close = request

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
            needs_bank_type=self._needs_bank_type,
            can_recheck_escalation=self._can_recheck,
            escalation_draft_id=self._escalation_draft_id,
            awaiting_filing=self._awaiting_filing,
            filing_deadline_passed=self._filing_deadline_passed,
            can_close=self._can_close,
            watching_thread=self._watching,
            can_resolve=self._awaiting_bank or self._late,
            last_thread_check=self._last_thread_check,
        )

    # --- main flow --------------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: CaseInput) -> CaseOutcome:
        if inp.resume is not None:  # continued as new while waiting for the bank
            return await self._resume_at_bank(inp, inp.resume)
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
                    AssessInput(case_id=inp.case_id, as_of=self._today()),
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
                    ended = await self._after_bank(inp, result)
                    if ended is not None:
                        return ended

        # Cases that predate a step keep waiting here until the user ends them.
        await workflow.wait_condition(lambda: self._abandoned)
        assert self._abandon is not None
        return await self._end(inp, self._abandon.reason, Actor.USER)

    # --- drafting -------------------------------------------------------------------------------

    async def _draft_until_approved(
        self, inp: CaseInput, kind: DraftKind = DraftKind.BANK_COMPLAINT
    ) -> bool:
        """Draft, then wait for the user's decision; redraft on rejection. False = abandoned
        (or the draft limit was reached and the case was abandoned while waiting).

        Drafts of both kinds share one numbering (`seq`); the limit applies per kind.
        """
        bank = kind is DraftKind.BANK_COMPLAINT
        while True:
            used = self._drafts_used if bank else self._escalation_drafts
            if used >= MAX_DRAFTS_PER_CASE:
                self._last_error = (
                    f"The limit of {MAX_DRAFTS_PER_CASE} drafts for this case was reached."
                )
                await workflow.wait_condition(lambda: self._abandoned)
                return False
            self._drafts_used += 1
            if not bank:
                self._escalation_drafts += 1
            summary = await self._run_draft(inp, self._drafts_used, kind)
            if summary is None or summary.status is DraftStatus.FAILED:
                # Fail closed: no letter. The user may ask for another try.
                if summary is not None:
                    self._last_error = "A checked draft could not be produced. Please try again."
                if bank:
                    self._status = CaseStatus.ASSESSED
                self._can_redraft = True
                await workflow.wait_condition(lambda: self._redraft or self._abandoned)
                self._can_redraft = self._redraft = False
                if self._abandoned:
                    return False
                self._last_error = None
                continue

            self._status = (
                CaseStatus.AWAITING_APPROVAL if bank else CaseStatus.ESCALATION_DRAFT_READY
            )
            self._draft_id = summary.draft_id
            self._last_error = None
            outcome = await self._wait_for_decision(inp, summary.draft_id)
            if outcome == "abandoned":
                return False
            if outcome == "approved":
                if bank:
                    self._approved_draft_id = summary.draft_id
                else:
                    self._escalation_draft_id = summary.draft_id
                return True
            # rejected → draft again
            if bank:
                self._status = CaseStatus.ASSESSED
            self._draft_id = None

    # --- sending ----------------------------------------------------------------------------------

    async def _send_approved(self, inp: CaseInput) -> bool:
        """Prepare the approved draft for sending; for ManualChannel wait until the user says
        it was sent. False = abandoned."""
        assert self._approved_draft_id is not None
        retried = False
        while True:
            summary = await self._prepare_send(inp, self._approved_draft_id)
            if (
                summary is not None
                and summary.channel is ChannelKind.GMAIL
                and summary.status in (OutboundStatus.PREPARED, OutboundStatus.SENDING)
                and workflow.patched("gmail-v1")
            ):
                # GmailChannel: send now (the approval was the user's go-ahead for this email).
                self._email_id = summary.email_id
                summary = await self._send_gmail(inp, summary.email_id, force=retried)
            if summary is not None:
                self._email_id = summary.email_id
                if summary.status is OutboundStatus.SENT:
                    self._watching = self._via_gmail = summary.channel is ChannelKind.GMAIL
                    return True
                if (
                    summary.status is OutboundStatus.PREPARED
                    and summary.channel is ChannelKind.MANUAL
                ):
                    break
                if summary.status is OutboundStatus.BLOCKED:
                    self._last_error = " ".join(summary.blocked_reasons)
            # Blocked (or failed): wait until the user fixed the cause and asks to retry.
            self._can_retry_send = True
            await workflow.wait_condition(lambda: self._retry_send or self._abandoned)
            self._can_retry_send = self._retry_send = False
            if self._abandoned:
                return False
            self._last_error = None
            retried = True

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

    async def _send_gmail(
        self, inp: CaseInput, email_id: uuid.UUID, *, force: bool
    ) -> SendSummary | None:
        try:
            summary: SendSummary = await workflow.execute_activity(
                SEND_EMAIL,
                SendEmailInput(case_id=inp.case_id, email_id=email_id, force=force),
                result_type=SendSummary,
                start_to_close_timeout=GMAIL_TIMEOUT,
                retry_policy=DB_RETRY,
            )
        except ActivityError as err:
            self._last_error = _error_message(err)
            return None
        return summary

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

    async def _run_draft(self, inp: CaseInput, seq: int, kind: DraftKind) -> DraftSummary | None:
        try:
            summary: DraftSummary = await workflow.execute_activity(
                DRAFT,
                DraftInput(
                    case_id=inp.case_id,
                    seq=seq,
                    letter_date=self._today(),
                    kind=kind,
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

    async def _wait_for_bank(self, inp: CaseInput, resume: ResumeAtBank | None = None) -> str:
        """AWAITING_BANK until the Ombudsman waiting period ends, handling the bank's replies:
        pasted ones (signals), and, with GmailChannel, new messages in the email's thread,
        checked every `gmail_poll_seconds`. Returns "abandoned", "resolved" or "escalate".
        """
        poll = timedelta(seconds=inp.time_scale.gmail_poll_seconds)
        if resume is None:
            summary = await self._assess_escalation(
                inp, "bank.wait_started", CaseStatus.AWAITING_BANK
            )
            # Without a window (activity error) there is no timer: replies are still handled.
            wait_days = summary.wait_days if summary is not None else 0
            deadline = (
                workflow.now() + days(wait_days, _scale(inp)) if summary is not None else None
            )
            judged = skipped = False
            next_poll = workflow.now() + poll if self._watching else None
        else:
            wait_days, deadline, judged = resume.wait_days, resume.deadline, resume.judged
            skipped, next_poll = resume.skipped, resume.next_poll
        self._status = CaseStatus.JUDGED if judged else CaseStatus.AWAITING_BANK
        self._awaiting_bank = True
        polls = 0

        def woken() -> bool:
            return bool(self._replies or self._resolved) or self._escalate or self._abandoned

        while True:
            now = workflow.now()
            waits = [t - now for t in (None if judged else deadline, next_poll) if t is not None]
            timeout = min(waits) if waits else None
            if timeout is None or timeout > timedelta(0):
                try:
                    await workflow.wait_condition(woken, timeout=timeout)
                except TimeoutError:
                    pass
            now = workflow.now()
            if not judged and deadline is not None and now >= deadline:
                self._bank_wait_expired = True
            if self._bank_wait_expired and not skipped:
                skipped = True  # count the skipped demo days once
                self._demo_days_passed(inp, wait_days)

            if self._abandoned:
                return "abandoned"
            if self._resolved:
                if await self._record_resolved(inp, self._resolved.pop(0)):
                    self._awaiting_bank = self._can_escalate = self._watching = False
                    self._status = CaseStatus.RESOLVED
                    return "resolved"
                continue
            if self._escalate:
                self._escalate = False
                await self._assess_escalation(inp, "case.escalation_chosen", None)
                break
            if next_poll is not None and now >= next_poll and not self._replies:
                # GmailChannel: read the thread (in an activity); new messages become replies.
                checked = await self._check_thread(inp)
                polls += 1
                if checked is not None and not checked.connected:
                    next_poll, self._watching = None, False  # paste replies from now on
                else:
                    if checked is not None:
                        self._replies.extend(BankReplied(reply_id=r) for r in checked.new_reply_ids)
                    next_poll = workflow.now() + poll
                if (
                    not woken()
                    and not self._bank_wait_expired
                    and (
                        polls >= inp.time_scale.max_polls_per_run
                        or workflow.info().is_continue_as_new_suggested()
                    )
                ):
                    # Keep the history short: carry the wait over into a fresh run.
                    assert self._email_id is not None
                    workflow.continue_as_new(
                        CaseInput(
                            case_id=inp.case_id,
                            upload_id=inp.upload_id,
                            time_scale=inp.time_scale,
                            resume=ResumeAtBank(
                                email_id=self._email_id,
                                deadline=deadline,
                                next_poll=next_poll,
                                judged=judged,
                                skipped=skipped,
                                wait_days=wait_days,
                                drafts_used=self._drafts_used,
                                demo_offset_days=self._demo_offset_days,
                                eligible_from=self._eligible_from,
                                filing_deadline=self._filing_deadline,
                                last_reply=self._last_reply,
                                can_escalate=self._can_escalate,
                            ),
                        )
                    )
                if self._replies:
                    continue
            if self._replies:
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
                continue
            if self._bank_wait_expired:
                await self._assess_escalation(inp, "bank.wait_expired", None)
                break

        self._awaiting_bank = self._can_escalate = self._watching = False
        return "escalate"

    async def _check_thread(self, inp: CaseInput) -> ThreadSummary | None:
        assert self._email_id is not None
        try:
            summary: ThreadSummary = await workflow.execute_activity(
                CHECK_THREAD,
                CheckThreadInput(case_id=inp.case_id, email_id=self._email_id),
                result_type=ThreadSummary,
                start_to_close_timeout=GMAIL_TIMEOUT,
                retry_policy=GMAIL_RETRY,
            )
        except ActivityError:
            return None  # Gmail had a bad moment; the next check tries again
        self._last_thread_check = workflow.now()
        return summary

    async def _after_bank(self, inp: CaseInput, result: str) -> CaseOutcome | None:
        """What follows the bank wait. None = the case predates escalation (keep waiting)."""
        if result == "abandoned":
            assert self._abandon is not None
            return await self._end(inp, self._abandon.reason, Actor.USER)
        if result == "resolved":
            return CaseOutcome(status=CaseStatus.RESOLVED)
        # 7. Escalate to the RBI Ombudsman: draft, approve, the user files.
        if workflow.patched("escalation-v1"):
            if workflow.patched("late-replies-v1"):
                return await self._escalate_with_late_replies(inp)
            ended = await self._go_to_ombudsman(inp)
            if ended is None:
                assert self._abandon is not None
                return await self._end(inp, self._abandon.reason, Actor.USER)
            return ended
        return None

    async def _resume_at_bank(self, inp: CaseInput, r: ResumeAtBank) -> CaseOutcome:
        """A run continued as new while waiting for the bank: restore and carry on."""
        self._email_id = r.email_id
        self._drafts_used = r.drafts_used
        self._demo_offset_days = r.demo_offset_days
        self._eligible_from, self._filing_deadline = r.eligible_from, r.filing_deadline
        self._last_reply, self._can_escalate = r.last_reply, r.can_escalate
        self._watching = self._via_gmail = r.next_poll is not None
        result = await self._wait_for_bank(inp, r)
        ended = await self._after_bank(inp, result)
        if ended is not None:
            return ended
        await workflow.wait_condition(lambda: self._abandoned)
        assert self._abandon is not None
        return await self._end(inp, self._abandon.reason, Actor.USER)

    # --- escalation to the RBI Ombudsman ----------------------------------------------------------

    async def _go_to_ombudsman(self, inp: CaseInput) -> CaseOutcome | None:
        """Check the case can go to the Ombudsman, draft the complaint, wait for the user to
        approve and file it, then for the outcome. None = abandoned."""
        summary = await self._eligible(inp)
        if summary is None:
            return None
        if not await self._draft_until_approved(inp, DraftKind.OMBUDSMAN_COMPLAINT):
            return None
        if not await self._wait_for_filing(inp, summary):
            return None

        self._status = CaseStatus.FILED_WITH_OMBUDSMAN
        self._can_close = True
        while True:
            await workflow.wait_condition(lambda: self._close is not None or self._abandoned)
            if self._abandoned:
                return None
            request = self._close
            assert request is not None
            try:
                await workflow.execute_activity(
                    RECORD_CLOSED,
                    RecordClosedInput(case_id=inp.case_id, request=request),
                    start_to_close_timeout=DB_TIMEOUT,
                    retry_policy=DB_RETRY,
                )
            except ActivityError as err:
                self._last_error = _error_message(err)
                self._close = None
                continue
            self._can_close = False
            self._last_error = None
            self._status = CaseStatus.CLOSED
            return CaseOutcome(status=CaseStatus.CLOSED, reason=request.outcome.value)

    async def _eligible(self, inp: CaseInput) -> EscalationSummary | None:
        """Wait until a complaint can be filed now. None = abandoned."""
        while True:
            summary = await self._assess_escalation(inp, "escalation.checked", None)
            problem: str | None = None
            if summary is None:
                problem = self._last_error
            elif summary.needs_bank_type:
                self._needs_bank_type = True
                problem = (
                    "Tell us what kind of bank this is, so we can check the Ombudsman covers it."
                )
            elif summary.entity_coverage == "NOT_COVERED":
                problem = "The RBI Ombudsman scheme does not cover this kind of bank."
            elif summary.can_file_now:
                self._needs_bank_type = False
                self._last_error = None
                return summary
            elif summary.wait_days > 0:
                # Not eligible yet (e.g. escalated before the waiting period ended): wait.
                try:
                    await workflow.wait_condition(
                        lambda: self._abandoned, timeout=days(summary.wait_days, _scale(inp))
                    )
                except TimeoutError:
                    self._demo_days_passed(inp, summary.wait_days)
                if self._abandoned:
                    return None
                continue
            else:
                problem = "The last day to complain to the RBI Ombudsman has passed."
            self._last_error = problem
            self._can_recheck = True
            await workflow.wait_condition(lambda: self._recheck or self._abandoned)
            self._can_recheck = self._recheck = False
            if self._abandoned:
                return None

    async def _wait_for_filing(self, inp: CaseInput, summary: EscalationSummary) -> bool:
        """Wait for `mark_filed`; remind when the filing deadline passes. False = abandoned."""
        assert self._escalation_draft_id is not None
        self._awaiting_filing = True
        deadline, left = None, 0
        if summary.filing_deadline is not None:
            left = (summary.filing_deadline - self._today()).days + 1  # through the last day
            deadline = workflow.now() + days(max(0, left), _scale(inp))
        while True:
            timeout = None
            if deadline is not None and not self._filing_deadline_passed:
                timeout = deadline - workflow.now()
                if timeout <= timedelta(0):
                    self._filing_deadline_passed = True
                    timeout = None
            try:
                await workflow.wait_condition(
                    lambda: bool(self._filings) or self._abandoned, timeout=timeout
                )
            except TimeoutError:
                self._filing_deadline_passed = True
                self._demo_days_passed(inp, max(0, left))
                continue
            if self._abandoned:
                return False
            mark = self._filings.pop(0)
            try:
                await workflow.execute_activity(
                    RECORD_FILED,
                    RecordFiledInput(
                        case_id=inp.case_id,
                        draft_id=self._escalation_draft_id,
                        mark=mark,
                        now=workflow.now(),
                    ),
                    start_to_close_timeout=DB_TIMEOUT,
                    retry_policy=DB_RETRY,
                )
            except ActivityError as err:
                self._last_error = _error_message(err)
                continue
            self._awaiting_filing = False
            self._last_error = None
            return True

    async def _escalate_with_late_replies(self, inp: CaseInput) -> CaseOutcome:
        """The Ombudsman steps, while the bank can still answer: replies after the window (from
        the Gmail thread, or pasted) are judged and shown, and "I got my money" ends the case
        as RESOLVED at any step."""
        self._late = True
        self._watching = self._via_gmail
        escalation = asyncio.create_task(self._go_to_ombudsman(inp))
        watcher = asyncio.create_task(self._watch_late_replies(inp))
        try:
            while True:
                await workflow.wait_condition(lambda: escalation.done() or bool(self._resolved))
                if escalation.done():
                    ended = escalation.result()
                    if ended is None:
                        assert self._abandon is not None
                        return await self._end(inp, self._abandon.reason, Actor.USER)
                    return ended
                if await self._record_resolved(inp, self._resolved.pop(0)):
                    escalation.cancel()
                    try:
                        await escalation
                    except asyncio.CancelledError:
                        pass
                    self._status = CaseStatus.RESOLVED
                    self._awaiting_filing = self._can_close = self._can_recheck = False
                    self._draft_id = None
                    return CaseOutcome(status=CaseStatus.RESOLVED)
        finally:
            self._late = self._watching = False
            watcher.cancel()

    async def _watch_late_replies(self, inp: CaseInput) -> None:
        """Read the thread now and then, and judge pasted or found replies, without changing the
        Ombudsman steps. Checks less often outside demo mode (months of waiting)."""
        poll = timedelta(seconds=inp.time_scale.gmail_poll_seconds)
        if not inp.time_scale.demo_mode:
            poll = max(poll, LATE_POLL)
        checks = 0
        while True:
            timeout = poll if self._watching and checks < MAX_LATE_CHECKS else None
            try:
                await workflow.wait_condition(lambda: bool(self._replies), timeout=timeout)
            except TimeoutError:
                checked = await self._check_thread(inp)
                checks += 1
                if checked is not None and not checked.connected:
                    self._watching = False
                elif checked is not None:
                    self._replies.extend(BankReplied(reply_id=r) for r in checked.new_reply_ids)
            while self._replies:
                reply = await self._judge(inp, self._replies.pop(0), late=True)
                if reply is None:
                    continue
                self._last_reply = reply
                if not reply.suspicious:  # a genuine reply moves the filing deadline (RB-IOS)
                    await self._assess_escalation(inp, "bank.replied_late", None)

    async def _judge(
        self, inp: CaseInput, reply: BankReplied, late: bool = False
    ) -> ReplySummary | None:
        try:
            summary: ReplySummary = await workflow.execute_activity(
                JUDGE_REPLY,
                JudgeReplyInput(case_id=inp.case_id, reply_id=reply.reply_id, late=late),
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
                    as_of=self._today(),
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

    # --- the case's calendar -------------------------------------------------------------------

    def _today(self) -> date:
        """The case's calendar date (IST): real today, plus the days that demo timers skipped.

        In DEMO_MODE a timer of n "days" lasts n * DEMO_DAY_SECONDS, but the calendar would not
        move, so the rule engine would still see the waiting period as unfinished. Each completed
        demo timer therefore moves the case's calendar on by its days. Outside demo mode the
        offset stays 0. Replay-safe: the offset is built only from recorded timers.
        """
        return workflow.now().astimezone(IST).date() + timedelta(days=self._demo_offset_days)

    def _demo_days_passed(self, inp: CaseInput, n: int) -> None:
        if inp.time_scale.demo_mode:
            self._demo_offset_days += n

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
