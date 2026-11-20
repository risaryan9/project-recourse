"""Google OAuth client and token encryption, against a mocked Google (no network)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from recourse.config import Settings
from recourse.email.crypto import TokenCipher, TokenCipherError
from recourse.email.oauth import SCOPE_READ, SCOPE_SEND, SCOPES, GoogleOAuth, OAuthError, new_pkce

CLIENT_ID = "client-123.apps.googleusercontent.com"
SETTINGS = Settings(
    google_client_id=CLIENT_ID,
    google_client_secret=SecretStr("secret"),
    google_redirect_uri="http://localhost:8000/auth/google/callback",
)


def id_token(**claims: Any) -> str:
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "email": "Asha@Example.com",
        "email_verified": True,
    }
    payload = base64.urlsafe_b64encode(json.dumps(base | claims).encode()).rstrip(b"=").decode()
    return f"h.{payload}.sig"


def google(
    token_body: dict[str, Any], status: int = 200
) -> tuple[GoogleOAuth, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=token_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GoogleOAuth(SETTINGS, client=client), seen


GOOD = {
    "access_token": "at",
    "refresh_token": "rt-secret",
    "expires_in": 3599,
    "scope": f"openid email {SCOPE_SEND} {SCOPE_READ}",
    "id_token": id_token(),
}


def test_authorization_url_asks_for_exactly_the_four_scopes() -> None:
    oauth, _ = google(GOOD)
    q = parse_qs(urlparse(oauth.authorization_url("st", "ch")).query)
    assert q["scope"] == [" ".join(SCOPES)]
    assert set(SCOPES) == {"openid", "email", SCOPE_SEND, SCOPE_READ}
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] == ["st"]


def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    verifier, challenge = new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode() and 43 <= len(verifier) <= 128


async def test_exchange_returns_the_grant() -> None:
    oauth, seen = google(GOOD)
    grant = await oauth.exchange("code-1", "verifier-1")
    assert grant.email == "asha@example.com" and grant.refresh_token == "rt-secret"
    sent = parse_qs(seen[0].content.decode())
    assert sent["code_verifier"] == ["verifier-1"] and sent["grant_type"] == ["authorization_code"]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (GOOD | {"scope": f"openid email {SCOPE_SEND}"}, "scopes_missing"),
        (GOOD | {"refresh_token": None}, "no_refresh_token"),
        (GOOD | {"id_token": id_token(aud="someone-else")}, "bad_id_token"),
        (GOOD | {"id_token": id_token(iss="https://evil.example")}, "bad_id_token"),
        (GOOD | {"id_token": id_token(email_verified=False)}, "email_unverified"),
    ],
)
async def test_bad_grants_are_refused(body: dict[str, Any], reason: str) -> None:
    oauth, _ = google(body)
    with pytest.raises(OAuthError) as e:
        await oauth.exchange("c", "v")
    assert e.value.reason == reason


async def test_google_refusal_is_reported() -> None:
    oauth, _ = google({"error": "invalid_grant"}, status=400)
    with pytest.raises(OAuthError, match="refused"):
        await oauth.exchange("c", "v")


def test_not_configured() -> None:
    with pytest.raises(OAuthError):
        GoogleOAuth(Settings(google_client_id=None))


def test_token_cipher_round_trip_and_bad_keys() -> None:
    key = Fernet.generate_key().decode()
    cipher = TokenCipher(key)
    blob = cipher.encrypt("rt-secret")
    assert "rt-secret" not in blob and cipher.decrypt(blob) == "rt-secret"
    with pytest.raises(TokenCipherError):
        TokenCipher(Fernet.generate_key().decode()).decrypt(blob)  # another key
    with pytest.raises(TokenCipherError):
        TokenCipher("not-a-key")
    with pytest.raises(TokenCipherError):
        TokenCipher.from_settings(Settings(token_encryption_key=None))
