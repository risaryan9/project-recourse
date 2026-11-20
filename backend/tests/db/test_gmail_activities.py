"""GmailChannel activities against Postgres, with a mocked Google (no real account, no network).

Run with `uv run pytest -m db`. Synthetic data only.
"""

from __future__ import annotations

import base64
import email
import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio.exceptions import ApplicationError

from recourse.agents.reply_judge_graph import CLASSIFY_PURPOSE, NextAction, SenderCheck
from recourse.config import Settings
from recourse.db.models import (
    BankReply,
    Case,
    CaseStatus,
    GmailConnection,
    OutboundEmail,
    OutboundStatus,
)
from recourse.email.channel import ChannelKind
from recourse.email.crypto import TokenCipher
from recourse.email.oauth import TOKEN_URL
from recourse.llm.fake import FakeLLM
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_types import (
    CheckThreadInput,
    JudgeReplyInput,
    PrepareSendInput,
    SendEmailInput,
    SendSummary,
)
from tests.db.test_case_activities import NOW, events, run
from tests.db.test_send_activities import approved_draft

pytestmark = pytest.mark.db

KEY = Fernet.generate_key().decode()
OWNER = "o" * 64
USER = "asha.user@example.com"
BANK = "bank-sandbox@example.com"  # SANDBOX_RECIPIENT: the "Sandbox Bank"
SETTINGS = Settings(
    email_sandbox=True,
    sandbox_recipient=BANK,
    google_client_id="client-123.apps.googleusercontent.com",
    google_client_secret=SecretStr("secret"),
    google_redirect_uri="http://localhost:8000/auth/google/callback",
    token_encryption_key=SecretStr(KEY),
)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid: str, sender: str, text: str, ms: int) -> dict[str, Any]:
    return {
        "id": mid,
        "internalDate": str(ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Someone <{sender}>"},
                {"name": "Subject", "value": "Re: complaint"},
            ],
            "body": {"data": b64(text)},
        },
    }


THREAD = {
    "id": "t1",
    "messages": [
        message("m1", USER, "my letter", 1_795_000_000_000),
        message(
            "m2",
            BANK,
            (
                "Dear customer, your complaint is registered under ref CMP48213. "
                "OTP 482913 must never be shared.\n\n"
                "On Fri, 20 Nov 2026 Asha wrote:\n> I am owed Rs 2,950.00 under the rules"
            ),
            1_795_000_100_000,
        ),
        message("m3", USER, "Thanks, I'll wait.", 1_795_000_200_000),
        message(
            "m4",
            "refunds@bank-support.example",
            "Your refund of Rs 2950 is ready.",
            1_795_000_300_000,
        ),
    ],
}


class Google:
    """A fake Google: token refresh, messages.send, threads.get. Records every request."""

    def __init__(self, *, send_status: int = 200, token_status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.send_status = send_status
        self.token_status = token_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == TOKEN_URL:
            return httpx.Response(
                self.token_status, json={"access_token": "at", "expires_in": 3600}
            )
        if request.url.path.endswith("/messages/send"):
            return httpx.Response(self.send_status, json={"id": "m1", "threadId": "t1"})
        if request.url.path.endswith("/threads/t1"):
            return httpx.Response(200, json=THREAD)
        return httpx.Response(404)

    def sent_mime(self) -> email.message.Message:
        req = next(r for r in self.requests if r.url.path.endswith("/messages/send"))
        raw = json.loads(req.content)["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def acts(
    sessions: async_sessionmaker[AsyncSession], google: Google, llm: FakeLLM | None = None
) -> CaseActivities:
    client = httpx.AsyncClient(transport=httpx.MockTransport(google))
    return CaseActivities(sessions, llm or FakeLLM({}), settings=SETTINGS, http=client)


async def gmail_case(
    sessions: async_sessionmaker[AsyncSession], connected: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    case_id, draft_id = await approved_draft(sessions)
    async with sessions.begin() as s:
        case = await s.get(Case, case_id)
        assert case is not None
        case.owner_id = OWNER
        if connected and await s.get(GmailConnection, OWNER) is None:
            s.add(
                GmailConnection(
                    owner_id=OWNER,
                    email=USER,
                    refresh_token_enc=TokenCipher(KEY).encrypt("rt"),
                    scopes=[],
                )
            )
    return case_id, draft_id


async def prepared(
    sessions: async_sessionmaker[AsyncSession],
    a: CaseActivities,
    case_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> uuid.UUID:
    summary: SendSummary = await run(
        a.prepare_send, PrepareSendInput(case_id=case_id, draft_id=draft_id)
    )
    assert summary.status is OutboundStatus.PREPARED
    return summary.email_id


async def test_channel_is_gmail_only_when_connected(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    case_id, draft_id = await gmail_case(sessions, connected=False)
    s1 = await run(
        acts(sessions, Google()).prepare_send, PrepareSendInput(case_id=case_id, draft_id=draft_id)
    )
    assert s1.channel is ChannelKind.MANUAL
    case_id, draft_id = await gmail_case(sessions)
    s2 = await run(
        acts(sessions, Google()).prepare_send, PrepareSendInput(case_id=case_id, draft_id=draft_id)
    )
    assert s2.channel is ChannelKind.GMAIL and s2.status is OutboundStatus.PREPARED


async def test_send_through_gmail(sessions: async_sessionmaker[AsyncSession]) -> None:
    google = Google()
    a = acts(sessions, google)
    case_id, draft_id = await gmail_case(sessions)
    email_id = await prepared(sessions, a, case_id, draft_id)
    summary = await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))
    assert summary.status is OutboundStatus.SENT
    assert await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id)) == summary
    assert sum(r.url.path.endswith("/messages/send") for r in google.requests) == 1  # sent once

    mime = google.sent_mime()
    assert mime["To"] == BANK and mime["From"] == USER  # sandbox: never a real bank
    async with sessions() as s:
        row = await s.get(OutboundEmail, email_id)
        case = await s.get(Case, case_id)
        assert row is not None and case is not None
        assert f"[{case.case_tag}]" in mime["Subject"]
        assert (row.message_id, row.thread_id) == ("m1", "t1")
        assert case.status is CaseStatus.SENT_TO_BANK
        names = await events(s, case_id)
        assert ["gmail.oauth.refresh", "gmail.messages.send", "email.sent"] == names[-3:]


async def test_unconfirmed_send_is_never_repeated_without_the_user(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    google = Google(send_status=503)
    a = acts(sessions, google)
    case_id, draft_id = await gmail_case(sessions)
    email_id = await prepared(sessions, a, case_id, draft_id)
    with pytest.raises(ApplicationError, match="did not confirm"):
        await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))
    async with sessions() as s:
        row = await s.get(OutboundEmail, email_id)
        assert row is not None and row.status is OutboundStatus.SENDING
    with pytest.raises(ApplicationError, match="Sent folder"):  # an automatic retry
        await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))
    assert sum(r.url.path.endswith("/messages/send") for r in google.requests) == 1
    google.send_status = 200
    summary = await run(
        a.send_email, SendEmailInput(case_id=case_id, email_id=email_id, force=True)
    )
    assert summary.status is OutboundStatus.SENT


async def test_refused_send_goes_back_to_prepared(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = acts(sessions, Google(send_status=400))
    case_id, draft_id = await gmail_case(sessions)
    email_id = await prepared(sessions, a, case_id, draft_id)
    with pytest.raises(ApplicationError, match="refused"):
        await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))
    async with sessions() as s:
        row = await s.get(OutboundEmail, email_id)
        assert row is not None and row.status is OutboundStatus.PREPARED


async def test_revoked_access_falls_back_to_manual(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    a = acts(sessions, Google(token_status=400))
    case_id, draft_id = await gmail_case(sessions)
    email_id = await prepared(sessions, a, case_id, draft_id)
    summary = await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))
    assert summary.channel is ChannelKind.MANUAL and summary.status is OutboundStatus.PREPARED
    async with sessions() as s:
        names = await events(s, case_id)
        assert "gmail.oauth.refresh" in names and names[-1] == "email.fallback_manual"


async def test_thread_check_stores_each_new_message_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    google = Google()
    llm = FakeLLM(
        {CLASSIFY_PURPOSE: [{"label": "ACKNOWLEDGEMENT", "complaint_number": "CMP48213"}]}
    )
    a = acts(sessions, google, llm)
    case_id, draft_id = await gmail_case(sessions)
    email_id = await prepared(sessions, a, case_id, draft_id)
    await run(a.send_email, SendEmailInput(case_id=case_id, email_id=email_id))

    first = await run(a.check_thread, CheckThreadInput(case_id=case_id, email_id=email_id))
    assert first.connected and first.messages == 4 and len(first.new_reply_ids) == 2  # m2, m4
    again = await run(a.check_thread, CheckThreadInput(case_id=case_id, email_id=email_id))
    assert again.new_reply_ids == []  # processed once

    async with sessions() as s:
        replies = {
            r.gmail_message_id: r
            for r in await s.scalars(select(BankReply).where(BankReply.case_id == case_id))
        }
        assert set(replies) == {"m2", "m4"}  # the letter and the user's own reply are skipped
        bank = replies["m2"]
        assert "Rs 2,950.00" not in bank.body  # the quoted letter was cut
        assert "482913" not in bank.body and "SECRET_CODE" in bank.redactions
        assert bank.sender == BANK and bank.received_at > NOW - timedelta(days=400)
        names = await events(s, case_id)
        assert names.count("gmail.threads.get") == 2

    # The Sandbox Bank is exactly SANDBOX_RECIPIENT: m2 is from the bank, m4 is not.
    ok = await run(a.judge_reply, JudgeReplyInput(case_id=case_id, reply_id=replies["m2"].id))
    assert ok.next_action is NextAction.KEEP_WAITING and ok.complaint_number == "CMP48213"
    spoof = await run(a.judge_reply, JudgeReplyInput(case_id=case_id, reply_id=replies["m4"].id))
    assert spoof.suspicious and spoof.next_action is NextAction.WARN
    async with sessions() as s:
        row = await s.get(BankReply, replies["m4"].id)
        assert row is not None and row.verdict is not None
        assert row.verdict["sender_check"] == SenderCheck.MISMATCH.value
    assert len(llm.calls) == 1  # the spoofed message never reached the model
