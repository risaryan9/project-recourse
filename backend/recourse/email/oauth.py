"""Google OAuth 2.0 for GmailChannel: authorization code flow with PKCE, over plain httpx.

Scopes are exactly `openid`, `email`, `gmail.send` and `gmail.readonly` (CLAUDE.md §3); a
grant missing any Gmail scope is refused. `access_type=offline` + `prompt=consent` make Google
return a refresh token every time. No token is ever logged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from recourse.config import Settings

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = ["openid", "email", SCOPE_SEND, SCOPE_READ]  # nothing else, ever


class OAuthError(Exception):
    """The sign-in could not be completed. `reason` is a short, user-safe code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Grant:
    email: str
    refresh_token: str
    access_token: str
    expires_in: int
    scopes: list[str]


def new_pkce() -> tuple[str, str]:
    """(code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


def _claims(id_token: str) -> dict[str, object]:
    """The ID token's claims. It came straight from Google's token endpoint over TLS, so
    (per OpenID Connect Core 3.1.3.7) the signature check may be skipped; iss/aud are checked."""
    try:
        payload = id_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        data: dict[str, object] = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError) as e:
        raise OAuthError("bad_id_token", "Google's ID token could not be read") from e
    return data


class GoogleOAuth:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not (
            settings.google_client_id
            and settings.google_client_secret
            and settings.google_redirect_uri
        ):
            raise OAuthError(
                "not_configured", "GOOGLE_CLIENT_ID, _SECRET and _REDIRECT_URI must be set"
            )
        self._id = settings.google_client_id
        self._secret = settings.google_client_secret.get_secret_value()
        self._redirect = settings.google_redirect_uri
        self._client = client or httpx.AsyncClient(timeout=20)

    def authorization_url(self, state: str, code_challenge: str) -> str:
        return (
            AUTH_URL
            + "?"
            + urlencode(
                {
                    "client_id": self._id,
                    "redirect_uri": self._redirect,
                    "response_type": "code",
                    "scope": " ".join(SCOPES),
                    "access_type": "offline",
                    "prompt": "consent",
                    "include_granted_scopes": "false",
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def exchange(self, code: str, code_verifier: str) -> Grant:
        r = await self._client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": self._id,
                "client_secret": self._secret,
                "redirect_uri": self._redirect,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        log.info("google oauth token exchange status=%s", r.status_code)
        if r.status_code != 200:
            raise OAuthError("exchange_failed", f"Google refused the sign-in ({r.status_code})")
        body = r.json()
        granted = body.get("scope", "").split()
        missing = [s for s in (SCOPE_SEND, SCOPE_READ) if s not in granted]
        if missing:
            raise OAuthError("scopes_missing", "Allow both Gmail permissions to connect")
        if not body.get("refresh_token"):
            raise OAuthError("no_refresh_token", "Google returned no refresh token")
        claims = _claims(body.get("id_token", ""))
        if claims.get("iss") not in ISSUERS or claims.get("aud") != self._id:
            raise OAuthError("bad_id_token", "Google's ID token is not for this app")
        email = claims.get("email")
        if not isinstance(email, str) or claims.get("email_verified") is not True:
            raise OAuthError("email_unverified", "The Google account has no verified email")
        return Grant(
            email=email.lower(),
            refresh_token=body["refresh_token"],
            access_token=body["access_token"],
            expires_in=int(body.get("expires_in", 3600)),
            scopes=granted,
        )

    async def refresh(self, refresh_token: str) -> tuple[str, int]:
        """A fresh access token (and its lifetime in seconds) for a stored refresh token."""
        r = await self._client.post(
            TOKEN_URL,
            data={
                "client_id": self._id,
                "client_secret": self._secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        log.info("google oauth token refresh status=%s", r.status_code)
        if r.status_code != 200:
            raise OAuthError(
                "refresh_failed", "Gmail access has expired or was revoked; connect again"
            )
        body = r.json()
        return body["access_token"], int(body.get("expires_in", 3600))

    async def revoke(self, token: str) -> bool:
        """Revoke a token at Google (best effort: True if Google confirmed)."""
        try:
            r = await self._client.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            log.warning("google oauth revoke failed: network")
            return False
        log.info("google oauth revoke status=%s", r.status_code)
        return r.status_code == 200
