"""GmailChannel: sends approved letters from the user's Gmail and reads only their thread.

**Thread-only access** (CLAUDE.md §3). `gmail.readonly` technically allows reading the whole
mailbox; Recourse restricts itself in code. `GmailApi` is the only code that calls Gmail, and it
can reach exactly two endpoints:

    POST /gmail/v1/users/me/messages/send
    GET  /gmail/v1/users/me/threads/{threadId}

Any other request raises before it leaves the process (`tests/unit/test_gmail_access.py` proves
it). Every call is reported through `on_call`, which the activities write to `audit_events`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from recourse.email.channel import ChannelKind, OutgoingEmail, SendOutcome
from recourse.email.mime import ThreadMessage, build_raw, parse_thread

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# (method, path pattern) of every Gmail endpoint Recourse may call. Nothing else.
ALLOWED_ENDPOINTS: dict[str, tuple[str, re.Pattern[str]]] = {
    "messages.send": ("POST", re.compile(r"^/gmail/v1/users/me/messages/send$")),
    "threads.get": ("GET", re.compile(r"^/gmail/v1/users/me/threads/[A-Za-z0-9]{1,64}$")),
}

OnCall = Callable[[str, dict[str, Any]], None]


class GmailAccessError(RuntimeError):
    """A request outside ALLOWED_ENDPOINTS was attempted (a bug, never a user error)."""


class GmailHttpError(RuntimeError):
    def __init__(self, endpoint: str, status: int) -> None:
        super().__init__(f"Gmail {endpoint} failed with HTTP {status}")
        self.endpoint = endpoint
        self.status = status

    @property
    def certainly_not_sent(self) -> bool:
        """A 4xx means Gmail refused the request; a 5xx or no answer leaves it uncertain."""
        return 400 <= self.status < 500


class GmailApi:
    def __init__(
        self,
        access_token: Callable[[], Awaitable[str]],
        *,
        client: httpx.AsyncClient | None = None,
        on_call: OnCall | None = None,
    ) -> None:
        self._token = access_token
        self._client = client or httpx.AsyncClient(timeout=30)
        self._on_call = on_call or (lambda endpoint, info: None)

    async def _request(self, endpoint: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
        allowed = ALLOWED_ENDPOINTS.get(endpoint)
        if allowed is None or allowed[0] != method or not allowed[1].match(path):
            raise GmailAccessError(f"Gmail endpoint not allowed: {method} {path}")
        url = "https://gmail.googleapis.com" + path
        headers = {"Authorization": f"Bearer {await self._token()}"}
        try:
            r = await self._client.request(method, url, headers=headers, **kw)
        except httpx.HTTPError:
            self._on_call(endpoint, {"status": None, "error": "network"})
            raise
        if r.status_code >= 400:
            self._on_call(endpoint, {"status": r.status_code})
            raise GmailHttpError(endpoint, r.status_code)
        body: dict[str, Any] = r.json()
        info: dict[str, Any] = {"status": r.status_code}
        if endpoint == "messages.send":
            info |= {"message_id": body.get("id"), "thread_id": body.get("threadId")}
        else:
            info |= {"thread_id": body.get("id"), "messages": len(body.get("messages", []))}
        self._on_call(endpoint, info)
        return body

    async def send(self, raw: str) -> dict[str, Any]:
        return await self._request(
            "messages.send", "POST", "/gmail/v1/users/me/messages/send", json={"raw": raw}
        )

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._request(
            "threads.get",
            "GET",
            f"/gmail/v1/users/me/threads/{thread_id}",
            params={"format": "full"},
        )


class GmailChannel:
    """EmailChannel over GmailApi. Public surface: `send` and `get_thread`, nothing else."""

    kind = ChannelKind.GMAIL

    def __init__(self, api: GmailApi, sender: str) -> None:
        self._api = api
        self._sender = sender

    async def send(self, email: OutgoingEmail) -> SendOutcome:
        raw = build_raw(sender=self._sender, to=email.to, subject=email.subject, body=email.body)
        body = await self._api.send(raw)
        return SendOutcome(
            channel=ChannelKind.GMAIL,
            sent=True,
            message_id=body.get("id"),
            thread_id=body.get("threadId"),
        )

    async def get_thread(self, thread_id: str) -> list[ThreadMessage]:
        return parse_thread(await self._api.get_thread(thread_id))
