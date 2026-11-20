"""Proof of thread-only Gmail access (CLAUDE.md §3, §5).

`gmail.readonly` would allow reading the whole mailbox; Recourse limits itself in code. These
tests fail if GmailChannel can reach any Gmail endpoint other than messages.send and
threads.get, or if any other module talks to Google.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from recourse.email.channel import OutgoingEmail
from recourse.email.gmail import ALLOWED_ENDPOINTS, GmailAccessError, GmailApi, GmailChannel

PACKAGE = Path(__file__).resolve().parents[2] / "recourse"
ALLOWED = {
    ("POST", "/gmail/v1/users/me/messages/send"),
    ("GET", "/gmail/v1/users/me/threads/t1"),
}


def strict_api() -> tuple[GmailApi, list[httpx.Request]]:
    """A Gmail that fails the test on any request outside the allow-list."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.method, request.url.path)
        if key not in ALLOWED:
            pytest.fail(f"GmailChannel called a forbidden endpoint: {key}")
        if key[0] == "POST":
            return httpx.Response(200, json={"id": "m1", "threadId": "t1"})
        return httpx.Response(200, json={"id": "t1", "messages": []})

    async def token() -> str:
        return "at"

    return GmailApi(token, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


async def test_channel_uses_only_send_and_thread_get() -> None:
    api, seen = strict_api()
    channel = GmailChannel(api, sender="user@example.com")
    await channel.send(
        OutgoingEmail(to="bank@example.com", subject="[RC-1] x", body="y", sandbox=True)
    )
    await channel.get_thread("t1")
    assert {(r.method, r.url.path) for r in seen} == ALLOWED


@pytest.mark.parametrize(
    ("endpoint", "method", "path"),
    [
        ("messages.list", "GET", "/gmail/v1/users/me/messages"),
        ("threads.list", "GET", "/gmail/v1/users/me/threads"),
        ("messages.get", "GET", "/gmail/v1/users/me/messages/m9"),
        ("history.list", "GET", "/gmail/v1/users/me/history"),
        ("settings", "GET", "/gmail/v1/users/me/settings/filters"),
        ("threads.get", "GET", "/gmail/v1/users/me/threads/../messages"),  # traversal
        ("threads.get", "DELETE", "/gmail/v1/users/me/threads/t1"),
        ("messages.send", "POST", "/gmail/v1/users/other@example.com/messages/send"),
    ],
)
async def test_any_other_endpoint_is_refused_before_it_is_sent(
    endpoint: str, method: str, path: str
) -> None:
    api, seen = strict_api()
    with pytest.raises(GmailAccessError):
        await api._request(endpoint, method, path)
    assert seen == []  # nothing left the process


def test_thread_ids_cannot_smuggle_a_path() -> None:
    api, _ = strict_api()
    for bad in ["t1/../../messages", "t1?q=from:x", "", "a" * 65]:
        with pytest.raises(GmailAccessError):
            import asyncio

            asyncio.run(api.get_thread(bad))


def test_the_allow_list_is_exactly_two_endpoints() -> None:
    assert set(ALLOWED_ENDPOINTS) == {"messages.send", "threads.get"}


def test_gmail_channel_exposes_only_send_and_get_thread() -> None:
    public = {n for n in dir(GmailChannel) if not n.startswith("_")}
    assert public == {"kind", "send", "get_thread"}
    api_public = {n for n in dir(GmailApi) if not n.startswith("_")}
    assert api_public == {"send", "get_thread"}


def test_only_the_gmail_and_oauth_modules_talk_to_google() -> None:
    allowed_files = {"email/gmail.py", "email/oauth.py"}
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).as_posix()
        text = path.read_text()
        if re.search(r"googleapis\.com|accounts\.google\.com", text):
            assert rel in allowed_files, f"{rel} talks to Google directly"
    gmail_src = (PACKAGE / "email" / "gmail.py").read_text()
    paths = set(re.findall(r"/gmail/v1/users/me/[a-z]+(?:/[a-z]+)?", gmail_src))
    assert paths <= {"/gmail/v1/users/me/messages/send", "/gmail/v1/users/me/threads"}
