"""Case API against Postgres (Neon test branch) with a fake CaseRunner (no Temporal).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.testing import ActivityEnvironment

from recourse.agents.draft_graph import DRAFT_PURPOSE
from recourse.agents.reply_judge_graph import NextAction, ReplyLabel
from recourse.api.deps import get_runner, get_session_factory
from recourse.api.main import create_app
from recourse.config import Settings
from recourse.db.models import BankReply, Case, CaseStatus, DraftRecord, Upload, UploadFile
from recourse.evidence.models import IST
from recourse.llm.fake import FakeLLM
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    AbandonRequest,
    AssessInput,
    BankReplied,
    CaseInput,
    CaseState,
    Confirmation,
    ConfirmInput,
    DraftDecisionInput,
    DraftInput,
    EscalationInput,
    MarkResolved,
    MarkSent,
    PrepareSendInput,
    ReplySummary,
    RunIntakeInput,
)
from tests.db.test_bank_wait_activities import sent_case
from tests.db.test_case_activities import NONE, sms_llm
from tests.unit.test_draft_graph import GOOD_FAILED

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)


@dataclass
class FakeRunner:
    started: list[CaseInput] = field(default_factory=list)
    confirmed: list[tuple[uuid.UUID, Confirmation]] = field(default_factory=list)
    abandoned: list[uuid.UUID] = field(default_factory=list)
    approved: list[uuid.UUID] = field(default_factory=list)
    rejected: list[uuid.UUID] = field(default_factory=list)
    redrafts: list[uuid.UUID] = field(default_factory=list)
    marks: list[MarkSent] = field(default_factory=list)
    retries: list[uuid.UUID] = field(default_factory=list)
    can_retry_send: bool = False
    fail_start: bool = False
    last_error: str | None = None
    can_redraft: bool = False
    replies: list[BankReplied] = field(default_factory=list)
    resolved: list[MarkResolved] = field(default_factory=list)
    escalations: list[uuid.UUID] = field(default_factory=list)
    can_escalate: bool = False
    last_reply: ReplySummary | None = None

    async def start(self, inp: CaseInput) -> None:
        if self.fail_start:
            raise RuntimeError("temporal down")
        self.started.append(inp)

    async def confirm(self, case_id: uuid.UUID, confirmation: Confirmation) -> None:
        self.confirmed.append((case_id, confirmation))

    async def abandon(self, case_id: uuid.UUID, request: AbandonRequest) -> None:
        self.abandoned.append(case_id)

    async def approve_draft(self, case_id: uuid.UUID, draft_id: uuid.UUID) -> None:
        self.approved.append(draft_id)

    async def reject_draft(self, case_id: uuid.UUID, draft_id: uuid.UUID) -> None:
        self.rejected.append(draft_id)

    async def redraft(self, case_id: uuid.UUID) -> None:
        self.redrafts.append(case_id)

    async def mark_sent(self, case_id: uuid.UUID, mark: MarkSent) -> None:
        self.marks.append(mark)

    async def retry_send(self, case_id: uuid.UUID) -> None:
        self.retries.append(case_id)

    async def bank_replied(self, case_id: uuid.UUID, reply: BankReplied) -> None:
        self.replies.append(reply)

    async def mark_resolved(self, case_id: uuid.UUID, mark: MarkResolved) -> None:
        self.resolved.append(mark)

    async def escalate(self, case_id: uuid.UUID) -> None:
        self.escalations.append(case_id)

    async def state(self, case_id: uuid.UUID) -> CaseState | None:
        return CaseState(
            status=CaseStatus.EXTRACTED,
            last_error=self.last_error,
            can_redraft=self.can_redraft,
            can_retry_send=self.can_retry_send,
            can_escalate=self.can_escalate,
            last_reply=self.last_reply,
        )


@pytest.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[httpx.AsyncClient, FakeRunner]]:
    app = create_app()
    runner = FakeRunner()
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_runner] = lambda: runner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, runner


async def intake(sessions: async_sessionmaker[AsyncSession], inp: CaseInput, **kw: Any) -> None:
    """Do what the worker would: run the intake activity for a started case."""
    acts = CaseActivities(sessions, sms_llm(**kw))
    await ActivityEnvironment().run(
        acts.run_intake, RunIntakeInput(case_id=inp.case_id, upload_id=inp.upload_id, now=NOW)
    )


async def test_create_case_stores_redacted_upload_and_starts_workflow(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    r = await client.post(
        "/cases",
        data={"text": "Rs 500 debited via UPI, OTP 482913"},
        files=[("files", ("shot.png", b"\x89PNG fake", "image/png"))],
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["case_tag"].startswith("RC-")
    [started] = runner.started
    assert str(started.case_id) == body["id"]

    async with sessions() as s:
        upload = await s.get(Upload, started.upload_id)
        assert upload is not None
        assert "482913" not in (upload.text or "")  # redacted before storage
        assert upload.redactions == ["SECRET_CODE"]
        files = (await s.scalars(select(UploadFile))).all()
        assert [f.media_type for f in files] == ["image/png"]


@pytest.mark.parametrize(
    ("data", "files", "code"),
    [
        ({}, None, 422),  # nothing submitted
        ({"text": "x"}, [("files", ("a.zip", b"PK", "application/zip"))], 422),
        (
            {},
            [("files", (f"{i}.png", b"\x89PNG", "image/png")) for i in range(6)],
            422,
        ),
    ],
)
async def test_create_case_rejects_bad_input(
    api: tuple[httpx.AsyncClient, FakeRunner],
    data: dict[str, str],
    files: list[Any] | None,
    code: int,
) -> None:
    client, runner = api
    r = await client.post("/cases", data=data, files=files)
    assert r.status_code == code
    assert runner.started == []


async def test_case_is_removed_if_the_workflow_cannot_start(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    runner.fail_start = True
    r = await client.post("/cases", data={"text": "Rs 500 debited via UPI"})
    assert r.status_code == 503
    async with sessions() as s:
        assert (await s.scalars(select(Case))).all() == []


async def test_full_flow_through_the_api(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    r = await client.post("/cases", data={"text": "Rs 2,450 debited via UPI, failed"})
    case_id = r.json()["id"]
    await intake(sessions, runner.started[0], utr=NONE)

    view = (await client.get(f"/cases/{case_id}")).json()
    assert view["status"] == "EXTRACTED"
    assert view["evidence"]["needs_confirmation"] == ["utr"]
    assert view["evidence"]["facts"]["amount_inr"]["value"] == "2450.00"
    assert [e["event_type"] for e in view["timeline"]] == ["case.created", "intake.completed"]

    # A bad value is rejected immediately, naming the field but not echoing the value.
    bad = await client.post(
        f"/cases/{case_id}/confirm", json={"corrections": {"amount_inr": "lots"}}
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["fields"] == ["amount_inr"]
    assert "lots" not in bad.text
    assert runner.confirmed == []

    # A good answer goes to the workflow, with a preview of the outcome.
    good = await client.post(
        f"/cases/{case_id}/confirm", json={"corrections": {"utr": "631845207719"}}
    )
    assert good.status_code == 202
    assert good.json() == {"needs_confirmation": [], "missing_details": [], "ready": True}
    [(signalled_id, confirmation)] = runner.confirmed
    assert str(signalled_id) == case_id

    # Do what the worker would, then read the assessed case.
    acts = CaseActivities(sessions, sms_llm())
    env = ActivityEnvironment()
    await env.run(
        acts.confirm, ConfirmInput(case_id=signalled_id, confirmation=confirmation, now=NOW)
    )
    await env.run(acts.assess, AssessInput(case_id=signalled_id, as_of=NOW.date()))

    view = (await client.get(f"/cases/{case_id}")).json()
    assert view["status"] == "ASSESSED"
    assert view["entitlement"]["covered"] is True
    assert view["entitlement"]["money"][0]["amount_inr"] == "2450.00"

    # Confirming again is refused: the case is no longer waiting for review.
    assert (await client.post(f"/cases/{case_id}/confirm", json={})).status_code == 409

    assert (await client.post(f"/cases/{case_id}/abandon")).status_code == 202
    assert runner.abandoned == [signalled_id]


async def test_unknown_case_is_404(api: tuple[httpx.AsyncClient, FakeRunner]) -> None:
    client, _ = api
    assert (await client.get(f"/cases/{uuid.uuid4()}")).status_code == 404
    assert (await client.post(f"/cases/{uuid.uuid4()}/confirm", json={})).status_code == 404


async def test_last_error_from_the_workflow_is_shown(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    r = await client.post("/cases", data={"text": "Rs 2,450 debited via UPI, failed"})
    await intake(sessions, runner.started[0])
    runner.last_error = "These facts cannot be assessed: reversal_date cannot be before txn_date"
    view = (await client.get(f"/cases/{r.json()['id']}")).json()
    assert view["last_error"] == runner.last_error


# =================================================================================================
# Drafts
# =================================================================================================


async def drafted_case(
    client: httpx.AsyncClient, runner: FakeRunner, sessions: async_sessionmaker[AsyncSession]
) -> tuple[str, str]:
    """A case with one pending draft, as the worker would leave it. Returns (case, draft) IDs."""
    r = await client.post("/cases", data={"text": "Rs 2,450 debited via UPI, failed"})
    case_id = r.json()["id"]
    inp = runner.started[-1]
    await intake(sessions, inp)
    env = ActivityEnvironment()
    acts = CaseActivities(sessions, FakeLLM({DRAFT_PURPOSE: [GOOD_FAILED.model_dump()]}))
    await env.run(
        acts.confirm, ConfirmInput(case_id=inp.case_id, confirmation=Confirmation(), now=NOW)
    )
    await env.run(acts.assess, AssessInput(case_id=inp.case_id, as_of=NOW.date()))
    summary = await env.run(
        acts.draft, DraftInput(case_id=inp.case_id, seq=1, letter_date=NOW.date())
    )
    return case_id, str(summary.draft_id)


async def test_approve_a_draft(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    case_id, draft_id = await drafted_case(client, runner, sessions)

    view = (await client.get(f"/cases/{case_id}")).json()
    assert view["status"] == "AWAITING_APPROVAL"
    draft = view["draft"]
    assert draft["id"] == draft_id and draft["status"] == "PENDING"
    assert draft["user_fill"] == ["customer.name"]
    assert "not legal advice" in draft["disclaimer"]
    assert draft["rules_verified"] is False
    assert (await client.get(f"/cases/{case_id}/draft")).json()["id"] == draft_id

    url = f"/cases/{case_id}/drafts/{draft_id}/approve"
    # An edit that changes an amount is refused at once, and nothing is signalled.
    bad_body = draft["body"].replace("₹2,450.00", "₹24,500.00")
    bad = await client.post(url, json={"customer_name": "Asha Verma", "body": bad_body})
    assert bad.status_code == 422
    assert any("amounts" in e for e in bad.json()["detail"]["errors"])
    assert runner.approved == []

    ok = await client.post(url, json={"customer_name": "Asha Verma"})
    assert ok.status_code == 202, ok.text
    assert ok.json()["final_body"].endswith("Asha Verma")
    assert [str(d) for d in runner.approved] == [draft_id]

    # Another draft ID is refused.
    other = f"/cases/{case_id}/drafts/{uuid.uuid4()}/approve"
    assert (await client.post(other, json={"customer_name": "Asha Verma"})).status_code == 409


async def test_reject_a_draft_stores_a_redacted_reason(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    case_id, draft_id = await drafted_case(client, runner, sessions)
    r = await client.post(
        f"/cases/{case_id}/drafts/{draft_id}/reject",
        json={"reason": "Shorter please. My OTP is 482913"},
    )
    assert r.status_code == 202
    assert [str(d) for d in runner.rejected] == [draft_id]
    async with sessions() as s:
        row = await s.get(DraftRecord, uuid.UUID(draft_id))
        assert row is not None and row.reject_reason is not None
        assert "Shorter please." in row.reject_reason
        assert "482913" not in row.reject_reason


async def test_redraft_only_when_the_workflow_waits_for_it(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    r = await client.post("/cases", data={"text": "Rs 2,450 debited via UPI, failed"})
    case_id = r.json()["id"]
    inp = runner.started[-1]
    await intake(sessions, inp)
    env = ActivityEnvironment()
    acts = CaseActivities(sessions, sms_llm())
    await env.run(
        acts.confirm, ConfirmInput(case_id=inp.case_id, confirmation=Confirmation(), now=NOW)
    )
    await env.run(acts.assess, AssessInput(case_id=inp.case_id, as_of=NOW.date()))

    assert (await client.post(f"/cases/{case_id}/redraft")).status_code == 409
    runner.can_redraft = True
    assert (await client.post(f"/cases/{case_id}/redraft")).status_code == 202
    assert runner.redrafts == [inp.case_id]


# =================================================================================================
# Sending
# =================================================================================================


async def test_bank_list_and_choice(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    banks = (await client.get("/banks")).json()
    assert {"pnb", "hdfc", "sbi"} <= {b["id"] for b in banks}
    assert not any(b["verified"] for b in banks)

    r = await client.post("/cases", data={"text": "Rs 2,450 debited via UPI, failed"})
    case_id = r.json()["id"]
    await intake(sessions, runner.started[-1])
    assert (await client.get(f"/cases/{case_id}")).json()["bank"] is None  # "Example Bank"

    assert (await client.put(f"/cases/{case_id}/bank", json={"bank_id": "nope"})).status_code == 422
    ok = await client.put(f"/cases/{case_id}/bank", json={"bank_id": "pnb"})
    assert ok.status_code == 200 and ok.json()["name"] == "Punjab National Bank"
    view = (await client.get(f"/cases/{case_id}")).json()
    assert view["bank"]["id"] == "pnb"
    assert view["timeline"][-1]["event_type"] == "case.bank_selected"


async def test_manual_send_through_the_api(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    case_id, draft_id = await drafted_case(client, runner, sessions)
    assert (await client.get(f"/cases/{case_id}/email")).status_code == 404
    assert (await client.post(f"/cases/{case_id}/email/mark-sent")).status_code == 409

    await client.post(
        f"/cases/{case_id}/drafts/{draft_id}/approve", json={"customer_name": "Asha Verma"}
    )
    # What the worker does after the approve_draft signal.
    acts = CaseActivities(
        sessions,
        FakeLLM({}),
        settings=Settings(email_sandbox=True, sandbox_recipient="sandbox@example.com"),
    )
    env = ActivityEnvironment()
    cid, did = uuid.UUID(case_id), uuid.UUID(draft_id)
    await env.run(acts.approve_draft, DraftDecisionInput(case_id=cid, draft_id=did, now=NOW))
    await env.run(acts.prepare_send, PrepareSendInput(case_id=cid, draft_id=did))

    email = (await client.get(f"/cases/{case_id}")).json()["email"]
    assert email["status"] == "PREPARED" and email["sandbox"] is True
    assert email["to"] == "sandbox@example.com"
    assert email["mailto"].startswith("mailto:sandbox@example.com?subject=")
    assert email["body"].endswith("Asha Verma")

    future = {"sent_at": "2099-01-01T10:00:00+05:30"}
    assert (await client.post(f"/cases/{case_id}/email/mark-sent", json=future)).status_code == 422
    r = await client.post(f"/cases/{case_id}/email/mark-sent")
    assert r.status_code == 202
    [mark] = runner.marks
    assert mark.sent_at.tzinfo is not None

    assert (await client.post(f"/cases/{case_id}/email/retry")).status_code == 409
    runner.can_retry_send = True
    assert (await client.post(f"/cases/{case_id}/email/retry")).status_code == 202


async def test_bank_reply_resolve_and_escalate_through_the_api(
    api: tuple[httpx.AsyncClient, FakeRunner], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, runner = api
    case_id = await sent_case(sessions)
    reply = {"text": "Complaint registered. OTP 482913 was used. Ref CMP48213."}
    # Not waiting for the bank yet (SENT_TO_BANK until the workflow starts the timer).
    assert (await client.post(f"/cases/{case_id}/bank-reply", json=reply)).status_code == 409

    acts = CaseActivities(sessions, FakeLLM({}))
    await ActivityEnvironment().run(
        acts.assess_escalation,
        EscalationInput(
            case_id=case_id,
            as_of=date(2026, 11, 17),
            event="bank.wait_started",
            new_status=CaseStatus.AWAITING_BANK,
        ),
    )
    view = (await client.get(f"/cases/{case_id}")).json()
    assert view["status"] == "AWAITING_BANK"
    assert view["bank_complained_on"] == "2026-11-17"
    assert view["escalation"]["eligible_from"] == "2026-12-18"

    early = reply | {"received_at": "2026-11-01T10:00:00+05:30"}
    assert (await client.post(f"/cases/{case_id}/bank-reply", json=early)).status_code == 422
    bad_sender = reply | {"sender": "not-an-address"}
    assert (await client.post(f"/cases/{case_id}/bank-reply", json=bad_sender)).status_code == 422

    r = await client.post(f"/cases/{case_id}/bank-reply", json=reply)
    assert r.status_code == 202, r.text
    assert r.json()["redactions"] == ["SECRET_CODE"]
    [signal] = runner.replies
    assert str(signal.reply_id) == r.json()["id"]
    [stored] = (await client.get(f"/cases/{case_id}/replies")).json()
    assert "482913" not in stored["text"] and stored["status"] == "PENDING"

    # Escalating needs the workflow to be asking.
    assert (await client.post(f"/cases/{case_id}/escalate")).status_code == 409
    runner.can_escalate = True
    runner.last_reply = ReplySummary(
        reply_id=signal.reply_id,
        label=ReplyLabel.REJECTED_WITH_REASONS,
        next_action=NextAction.ASK_USER,
        suspicious=False,
    )
    assert (await client.post(f"/cases/{case_id}/escalate")).status_code == 202
    assert runner.escalations == [case_id]
    async with sessions() as s:
        row = await s.get(BankReply, signal.reply_id)
        assert row is not None and row.unsatisfactory

    bad = {"amount_received_inr": "-1"}
    assert (await client.post(f"/cases/{case_id}/resolved", json=bad)).status_code == 422
    ok = {"amount_received_inr": "2750.00"}
    assert (await client.post(f"/cases/{case_id}/resolved", json=ok)).status_code == 202
    assert [m.amount_received_inr for m in runner.resolved] == [2750]
