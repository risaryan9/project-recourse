"""Connecting Gmail through the API, with a mocked Google (Neon test branch, no real account)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.api.deps import get_session_factory
from recourse.api.gmail import STATE_COOKIE, get_cipher, get_google_oauth
from recourse.api.main import create_app
from recourse.api.owner import OWNER_COOKIE, owner_hash
from recourse.config import Settings, get_settings
from recourse.db.models import GmailConnection
from recourse.email.crypto import TokenCipher
from recourse.email.oauth import REVOKE_URL, GoogleOAuth
from tests.unit.test_gmail_oauth import GOOD

pytestmark = pytest.mark.db

KEY = Fernet.generate_key().decode()
SETTINGS = Settings(
    google_client_id="client-123.apps.googleusercontent.com",
    google_client_secret=SecretStr("secret"),
    google_redirect_uri="http://localhost:8000/auth/google/callback",
    token_encryption_key=SecretStr(KEY),
    frontend_url="http://app.test",
)


@pytest.fixture
async def api(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[httpx.AsyncClient, list[httpx.Request]]]:
    calls: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == REVOKE_URL:
            return httpx.Response(200)
        return httpx.Response(200, json=GOOD)

    oauth = GoogleOAuth(SETTINGS, client=httpx.AsyncClient(transport=httpx.MockTransport(google)))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_session_factory] = lambda: sessions
    app.dependency_overrides[get_google_oauth] = lambda: oauth
    app.dependency_overrides[get_cipher] = lambda: TokenCipher(KEY)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, calls


async def connect(client: httpx.AsyncClient) -> httpx.Response:
    start = await client.get("/auth/google/start")
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return await client.get("/auth/google/callback", params={"code": "c1", "state": state})


async def test_connect_status_disconnect(
    api: tuple[httpx.AsyncClient, list[httpx.Request]], sessions: async_sessionmaker[AsyncSession]
) -> None:
    client, calls = api
    assert (await client.get("/auth/google/status")).json() == {
        "configured": True,
        "connected": False,
        "email": None,
    }

    start = await client.get("/auth/google/start")
    assert start.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert STATE_COOKIE in start.cookies and OWNER_COOKIE in start.cookies  # owner issued here

    r = await connect(client)
    assert (
        r.status_code == 302 and r.headers["location"] == "http://app.test/settings?gmail=connected"
    )
    status = (await client.get("/auth/google/status")).json()
    assert status == {"configured": True, "connected": True, "email": "asha@example.com"}

    async with sessions() as s:
        row = (await s.scalars(select(GmailConnection))).one()
        assert row.owner_id == owner_hash(client.cookies[OWNER_COOKIE])
        assert "rt-secret" not in row.refresh_token_enc  # encrypted at rest
        assert TokenCipher(KEY).decrypt(row.refresh_token_enc) == "rt-secret"

    r = await client.post("/auth/google/disconnect")
    assert r.json()["connected"] is False
    assert str(calls[-1].url) == REVOKE_URL and b"rt-secret" in calls[-1].content
    async with sessions() as s:
        assert (await s.scalars(select(GmailConnection))).first() is None


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"code": "c1", "state": "forged"}, "state_mismatch"),
        ({"code": "c1"}, "state_mismatch"),
        ({"error": "access_denied"}, "denied"),
    ],
)
async def test_bad_callbacks_store_nothing(
    api: tuple[httpx.AsyncClient, list[httpx.Request]],
    sessions: async_sessionmaker[AsyncSession],
    params: dict[str, Any],
    reason: str,
) -> None:
    client, _ = api
    await client.get("/auth/google/start")
    r = await client.get("/auth/google/callback", params=params)
    assert r.headers["location"] == f"http://app.test/settings?gmail=error&reason={reason}"
    async with sessions() as s:
        assert (await s.scalars(select(GmailConnection))).first() is None


async def test_another_browser_sees_no_connection(
    api: tuple[httpx.AsyncClient, list[httpx.Request]],
) -> None:
    client, _ = api
    await connect(client)
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    async with other:
        assert (await other.get("/auth/google/status")).json()["connected"] is False
        assert (await other.post("/auth/google/disconnect")).json()["connected"] is False
    assert (await client.get("/auth/google/status")).json()["connected"] is True
