"""Case API against Postgres (Neon test branch) with a fake CaseRunner (no Temporal).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.testing import ActivityEnvironment

from recourse.api.deps import get_runner, get_session_factory
from recourse.api.main import create_app
from recourse.db.models import Case, CaseStatus, Upload, UploadFile
from recourse.evidence.models import IST
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    AbandonRequest,
    AssessInput,
    CaseInput,
    CaseState,
    Confirmation,
    ConfirmInput,
    RunIntakeInput,
)
from tests.db.test_case_activities import NONE, sms_llm

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=IST)


@dataclass
class FakeRunner:
    started: list[CaseInput] = field(default_factory=list)
    confirmed: list[tuple[uuid.UUID, Confirmation]] = field(default_factory=list)
    abandoned: list[uuid.UUID] = field(default_factory=list)
    fail_start: bool = False
    last_error: str | None = None

    async def start(self, inp: CaseInput) -> None:
        if self.fail_start:
            raise RuntimeError("temporal down")
        self.started.append(inp)

    async def confirm(self, case_id: uuid.UUID, confirmation: Confirmation) -> None:
        self.confirmed.append((case_id, confirmation))

    async def abandon(self, case_id: uuid.UUID, request: AbandonRequest) -> None:
        self.abandoned.append(case_id)

    async def state(self, case_id: uuid.UUID) -> CaseState | None:
        return CaseState(status=CaseStatus.EXTRACTED, last_error=self.last_error)


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
