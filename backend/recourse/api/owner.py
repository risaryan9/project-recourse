"""Anonymous case ownership: a case belongs to the browser that created it.

There is no login. On its first request a browser gets a random token in an httpOnly cookie;
each case stores the SHA-256 hash of the token that created it (`cases.owner_id`), so a
database leak exposes no usable token. Every `/cases/{case_id}/...` route answers 404 for a
case the caller does not own, exactly as for one that does not exist.

Locally the frontend (localhost:3000) and the API (localhost:8000) are the same site, so a
`Lax` cookie works with `fetch(..., {credentials: "include"})`. A deployment on different
sites needs `OWNER_COOKIE_SAMESITE=none` and `OWNER_COOKIE_SECURE=true` (HTTPS).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.api.deps import get_session_factory
from recourse.config import get_settings
from recourse.db.models import Case

OWNER_COOKIE = "recourse_owner"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
ONE_YEAR = 365 * 24 * 3600


def owner_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def current_owner(request: Request) -> str | None:
    """The caller's owner ID (a hash), or None if this browser has no token yet.

    Reads never issue a token: on a first visit a page fires several requests at once, and
    each would mint its own token; a late response could then replace the token a case was
    just created with, and the browser would lose its own case. Only creating a case issues
    one (`get_owner`).
    """
    token = request.cookies.get(OWNER_COOKIE)
    if token is None or not _TOKEN_RE.match(token):
        return None
    return owner_hash(token)


def set_owner_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        OWNER_COOKIE,
        token,
        max_age=ONE_YEAR,
        httponly=True,
        secure=settings.owner_cookie_secure,
        samesite=settings.owner_cookie_samesite,
        path="/",
    )


def owner_or_new(request: Request) -> tuple[str, str | None]:
    """(owner ID, a new token to set) - the token is None if the browser already has one.
    For endpoints that return their own Response (e.g. a redirect), which must set it."""
    token = request.cookies.get(OWNER_COOKIE)
    if token is not None and _TOKEN_RE.match(token):
        return owner_hash(token), None
    token = secrets.token_urlsafe(32)
    return owner_hash(token), token


def get_owner(request: Request, response: Response) -> str:
    """The caller's owner ID, issuing a new token cookie if there is none (create only).
    Only for endpoints that return data: FastAPI drops this cookie if the endpoint returns its
    own Response object (use `owner_or_new` there)."""
    owner, new = owner_or_new(request)
    if new is not None:
        set_owner_cookie(response, new)
    return owner


Owner = Annotated[str, Depends(get_owner)]  # creating: issues a token if needed
Reader = Annotated[str | None, Depends(current_owner)]  # everything else: never issues one


async def require_case_owner(
    request: Request,
    owner: Reader,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> None:
    """Router dependency: a `{case_id}` the caller does not own is "not found"."""
    raw = request.path_params.get("case_id")
    if raw is None:
        return
    try:
        case_id = uuid.UUID(str(raw))
    except ValueError:
        return  # the route's own validation answers 422
    async with sessions() as s:
        found = await s.scalar(select(Case.owner_id).where(Case.id == case_id))
    if owner is None or found is None or not secrets.compare_digest(found, owner):
        raise HTTPException(404, "case not found")
