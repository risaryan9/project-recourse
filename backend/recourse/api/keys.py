"""Bring your own key (ADR-023): the visitor's model API keys, in this process's memory only.

* `GET /keys`: does this browser have a key here, and is one required?
* `PUT /keys`: check the keys with a free call, then hold them in memory for this browser.
* `DELETE /keys`: forget them now.
* `GET /internal/keys/{case_id}`: the worker's lookup over loopback (shared secret). Never
  served through the public proxy: anything with a forwarding header is refused.

Keys never reach the database, logs, Temporal or a response other than the internal one.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from recourse.api.deps import get_session
from recourse.api.owner import Owner, Reader
from recourse.config import Settings, get_settings
from recourse.db.models import Case
from recourse.llm.user_keys import (
    ANTHROPIC_PREFIX,
    MAX_KEY_LENGTH,
    NEEDS_KEY_MESSAGE,
    OPENAI_PREFIX,
    UserKeys,
    check_keys,
    clean_key,
    get_vault,
)

router = APIRouter(prefix="/keys", tags=["keys"])
internal_router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
Session = Annotated[AsyncSession, Depends(get_session)]


class KeyStatus(BaseModel):
    required: bool  # REQUIRE_USER_KEYS: cases need the visitor's own key
    anthropic: bool
    openai: bool
    expires_in_seconds: int | None


class KeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anthropic_key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)
    openai_key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)


def _status(settings: Settings, owner: str | None) -> KeyStatus:
    vault = get_vault(settings)
    keys = vault.get(owner) if owner else None
    return KeyStatus(
        required=settings.require_user_keys,
        anthropic=bool(keys and keys.anthropic),
        openai=bool(keys and keys.openai),
        expires_in_seconds=vault.expires_in(owner) if owner and keys else None,
    )


@router.get("")
async def key_status(owner: Reader, settings: SettingsDep) -> KeyStatus:
    return _status(settings, owner)


@router.put("")
async def save_keys(body: KeysIn, owner: Owner, settings: SettingsDep) -> KeyStatus:
    try:
        keys = UserKeys(
            anthropic=clean_key(body.anthropic_key, ANTHROPIC_PREFIX),
            openai=clean_key(body.openai_key, OPENAI_PREFIX),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    if not keys.any:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter an Anthropic or OpenAI key.")
    results = await check_keys(settings, keys)
    problems = [msg for msg in results.values() if msg != "ok"]
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " ".join(problems))
    get_vault(settings).put(owner, keys)
    return _status(settings, owner)


@router.delete("")
async def forget_keys(owner: Reader, settings: SettingsDep) -> KeyStatus:
    if owner:
        get_vault(settings).forget(owner)
    return _status(settings, owner)


def require_user_keys(owner: Reader, settings: SettingsDep) -> None:
    """For actions that start model work: with REQUIRE_USER_KEYS, the caller must have a key."""
    if not settings.require_user_keys:
        return
    if owner is None or get_vault(settings).get(owner) is None:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            {"code": "NEEDS_API_KEY", "message": NEEDS_KEY_MESSAGE},
        )


NeedsKeys = Depends(require_user_keys)


class InternalKeys(BaseModel):
    anthropic: str | None
    openai: str | None


@internal_router.get("/keys/{case_id}")
async def case_keys(
    case_id: uuid.UUID, request: Request, session: Session, settings: SettingsDep
) -> InternalKeys:
    secret = settings.internal_secret.get_secret_value() if settings.internal_secret else ""
    given = request.headers.get("x-internal-secret", "")
    forwarded = any(
        h in request.headers for h in ("x-forwarded-for", "x-forwarded-host", "forwarded")
    )
    if not secret or forwarded or not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(status.HTTP_404_NOT_FOUND)  # indistinguishable from "no such route"
    case = await session.get(Case, case_id)
    keys = get_vault(settings).get(case.owner_id) if case and case.owner_id else None
    if keys is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return InternalKeys(anthropic=keys.anthropic, openai=keys.openai)
