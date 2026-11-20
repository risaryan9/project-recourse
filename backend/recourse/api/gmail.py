"""Connecting Gmail (GmailChannel, phase 1): Google sign-in, status and disconnect.

    GET  /auth/google/start       -> 302 to Google (state + PKCE in a short-lived cookie)
    GET  /auth/google/callback    -> 302 back to the app's Settings page
    GET  /auth/google/status      -> {connected, email}
    POST /auth/google/disconnect  -> revokes at Google, deletes the stored token

The connection belongs to the browser's anonymous owner (ADR-017), like its cases.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.api.deps import get_session_factory
from recourse.api.owner import Reader, owner_or_new, set_owner_cookie
from recourse.config import Settings, get_settings
from recourse.db.models import GmailConnection
from recourse.email.crypto import TokenCipher, TokenCipherError
from recourse.email.oauth import GoogleOAuth, OAuthError, new_pkce

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["gmail"])

STATE_COOKIE = "recourse_oauth"
STATE_TTL = 600  # seconds to finish signing in


def get_google_oauth(settings: Annotated[Settings, Depends(get_settings)]) -> GoogleOAuth:
    try:
        return GoogleOAuth(settings)
    except OAuthError as e:
        raise HTTPException(503, "Gmail is not set up on this server") from e


def get_cipher(settings: Annotated[Settings, Depends(get_settings)]) -> TokenCipher:
    try:
        return TokenCipher.from_settings(settings)
    except TokenCipherError as e:
        raise HTTPException(503, "Gmail is not set up on this server") from e


OAuth = Annotated[GoogleOAuth, Depends(get_google_oauth)]
Cipher = Annotated[TokenCipher, Depends(get_cipher)]
Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


def _back(settings: Settings, **params: str) -> RedirectResponse:
    r = RedirectResponse(f"{settings.frontend_url}/settings?{urlencode(params)}", status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/auth/google")
    return r


@router.get("/start")
async def start(
    request: Request,
    oauth: OAuth,
    cipher: Cipher,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    """Begin Google sign-in. Issues the owner token too, if this browser has none yet."""
    del cipher  # resolved only to fail early if no encryption key is configured
    _, new_token = owner_or_new(request)  # the connection belongs to this browser's owner
    state = secrets.token_urlsafe(32)
    verifier, challenge = new_pkce()
    r = RedirectResponse(oauth.authorization_url(state, challenge), status_code=302)
    r.set_cookie(
        STATE_COOKIE,
        f"{state}.{verifier}",
        max_age=STATE_TTL,
        httponly=True,
        secure=settings.owner_cookie_secure,
        samesite="lax",  # sent on Google's top-level redirect back to the callback
        path="/auth/google",
    )
    if new_token is not None:  # set on the redirect itself (an injected Response is dropped)
        set_owner_cookie(r, new_token)
    return r


@router.get("/callback")
async def callback(
    request: Request,
    owner: Reader,
    oauth: OAuth,
    cipher: Cipher,
    sessions: Sessions,
    settings: Annotated[Settings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        return _back(
            settings, gmail="error", reason="denied" if error == "access_denied" else "google_error"
        )
    saved = request.cookies.get(STATE_COOKIE, "")
    saved_state, _, verifier = saved.partition(".")
    if not (code and state and saved_state and verifier) or not secrets.compare_digest(
        state, saved_state
    ):
        return _back(settings, gmail="error", reason="state_mismatch")
    if owner is None:
        return _back(settings, gmail="error", reason="no_owner")
    try:
        grant = await oauth.exchange(code, verifier)
    except OAuthError as e:
        log.info("gmail connect failed reason=%s", e.reason)
        return _back(settings, gmail="error", reason=e.reason)

    async with sessions.begin() as s:
        row = await s.get(GmailConnection, owner)
        if row is None:
            row = GmailConnection(owner_id=owner)
            s.add(row)
        row.email = grant.email
        row.refresh_token_enc = cipher.encrypt(grant.refresh_token)
        row.scopes = grant.scopes
    log.info("gmail connected")
    return _back(settings, gmail="connected")


class GmailStatus(BaseModel):
    configured: bool  # the server has Google credentials and an encryption key
    connected: bool
    email: str | None = None


@router.get("/status")
async def status(
    owner: Reader, sessions: Sessions, settings: Annotated[Settings, Depends(get_settings)]
) -> GmailStatus:
    configured = bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_redirect_uri
        and settings.token_encryption_key
    )
    if owner is None:
        return GmailStatus(configured=configured, connected=False)
    async with sessions() as s:
        row = await s.get(GmailConnection, owner)
    return GmailStatus(
        configured=configured, connected=row is not None, email=row.email if row else None
    )


@router.post("/disconnect")
async def disconnect(
    owner: Reader, oauth: OAuth, cipher: Cipher, sessions: Sessions
) -> GmailStatus:
    """Revoke the token at Google (best effort) and delete it here (always)."""
    if owner is None:
        return GmailStatus(configured=True, connected=False)
    async with sessions.begin() as s:
        row = await s.get(GmailConnection, owner, with_for_update=True)
        if row is None:
            return GmailStatus(configured=True, connected=False)
        try:
            token = cipher.decrypt(row.refresh_token_enc)
        except TokenCipherError:
            token = None
        await s.delete(row)
    if token:
        await oauth.revoke(token)
    log.info("gmail disconnected")
    return GmailStatus(configured=True, connected=False)
