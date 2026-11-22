"""Bring your own key (ADR-023): visitors' model API keys, held in memory only.

With `REQUIRE_USER_KEYS=true`, every model call for a case uses the key its owner (the browser
that created it) entered in the UI. Keys are:

* kept in the API process's memory (`KeyVault`), per owner, for `USER_KEY_TTL_MINUTES`;
* never written to the database, a file, a log, Temporal's history or an error message;
* lost on restart (the browser re-sends the key it keeps for the open tab).

The worker never receives a key through Temporal (workflow inputs and activity results are
stored in its history). An activity asks the API for the case's key over loopback with a shared
secret (`fetch_case_keys`), builds the clients for that one call, and drops them.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from recourse.config import Settings
from recourse.llm.base import LLMClient

ANTHROPIC_PREFIX = "sk-ant-"
OPENAI_PREFIX = "sk-"
MAX_KEY_LENGTH = 300


class MissingUserKeys(Exception):
    """This case's owner has no key in the API's memory (never entered, expired or restarted)."""


NEEDS_KEY_MESSAGE = "Add your AI key (Settings, or the box on this page), then try again."


@dataclass(frozen=True)
class UserKeys:
    anthropic: str | None = None
    openai: str | None = None

    def __repr__(self) -> str:  # never print a key, even in a traceback
        a = "set" if self.anthropic else None
        o = "set" if self.openai else None
        return f"UserKeys(anthropic={a}, openai={o})"

    __str__ = __repr__

    @property
    def any(self) -> bool:
        return bool(self.anthropic or self.openai)


def clean_key(value: str | None, prefix: str) -> str | None:
    """The key, trimmed; None if empty. ValueError (without the key) if it can't be one."""
    if value is None or not value.strip():
        return None
    key = value.strip()
    if not key.startswith(prefix) or len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise ValueError(f"That doesn't look like a key (it should start with {prefix}).")
    return key


class KeyVault:
    """Owner ID -> keys, in this process's memory only, each entry expiring after `ttl`."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[UserKeys, float]] = {}
        self._lock = threading.Lock()

    def put(self, owner: str, keys: UserKeys) -> None:
        with self._lock:
            self._entries[owner] = (keys, self._clock() + self._ttl)

    def get(self, owner: str) -> UserKeys | None:
        with self._lock:
            self._sweep()
            entry = self._entries.get(owner)
            return entry[0] if entry else None

    def expires_in(self, owner: str) -> int | None:
        with self._lock:
            self._sweep()
            entry = self._entries.get(owner)
            return max(0, int(entry[1] - self._clock())) if entry else None

    def forget(self, owner: str) -> None:
        with self._lock:
            self._entries.pop(owner, None)

    def _sweep(self) -> None:
        now = self._clock()
        for owner in [o for o, (_, exp) in self._entries.items() if exp <= now]:
            del self._entries[owner]


_vault: KeyVault | None = None


def get_vault(settings: Settings) -> KeyVault:
    global _vault
    if _vault is None:
        _vault = KeyVault(settings.user_key_ttl_minutes * 60)
    return _vault


def llm_from_keys(settings: Settings, keys: UserKeys) -> LLMClient:
    """A client for one call, using only the given keys, in `settings.llm_providers` order."""
    import anthropic
    import openai

    from recourse.llm.anthropic_client import AnthropicLLM
    from recourse.llm.fallback import FallbackLLM
    from recourse.llm.openai_client import OpenAILLM
    from recourse.tracing import wrap_client

    providers: list[tuple[str, LLMClient]] = []
    for name in settings.llm_providers:
        if name == "anthropic" and keys.anthropic:
            client = wrap_client(
                anthropic.AsyncAnthropic(
                    api_key=keys.anthropic,
                    max_retries=settings.anthropic_max_retries,
                    timeout=settings.anthropic_timeout_seconds,
                )
            )
            providers.append((name, AnthropicLLM(settings, client=client)))
        elif name == "openai" and keys.openai:
            oclient = wrap_client(
                openai.AsyncOpenAI(
                    api_key=keys.openai,
                    max_retries=settings.openai_max_retries,
                    timeout=settings.openai_timeout_seconds,
                )
            )
            providers.append((name, OpenAILLM(settings, client=oclient)))
    if not providers:
        raise MissingUserKeys
    return FallbackLLM(providers)


async def check_keys(settings: Settings, keys: UserKeys) -> dict[str, str]:
    """Try each given key with a free call (list models). Provider -> "ok" or a short reason."""
    import anthropic
    import openai

    out: dict[str, str] = {}
    if keys.anthropic:
        try:
            await anthropic.AsyncAnthropic(
                api_key=keys.anthropic, max_retries=0, timeout=15
            ).models.list(limit=1)
            out["anthropic"] = "ok"
        except anthropic.AuthenticationError:
            out["anthropic"] = "Anthropic rejected this key."
        except anthropic.PermissionDeniedError:
            out["anthropic"] = "This Anthropic key isn't allowed to use the API."
        except anthropic.APIError:
            out["anthropic"] = "Anthropic couldn't be reached to check the key. Try again."
    if keys.openai:
        try:
            await openai.AsyncOpenAI(api_key=keys.openai, max_retries=0, timeout=15).models.list()
            out["openai"] = "ok"
        except openai.AuthenticationError:
            out["openai"] = "OpenAI rejected this key."
        except openai.PermissionDeniedError:
            out["openai"] = "This OpenAI key isn't allowed to use the API."
        except openai.APIError:
            out["openai"] = "OpenAI couldn't be reached to check the key. Try again."
    return out


class CaseKeyResolver:
    """Worker side: the model client for one case, built from its owner's key in the API."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        if settings.internal_secret is None or not settings.internal_secret.get_secret_value():
            raise RuntimeError("REQUIRE_USER_KEYS needs INTERNAL_SECRET (shared by API and worker)")
        self._settings = settings
        self._http = http

    async def __call__(self, case_id: uuid.UUID) -> LLMClient:
        keys = await fetch_case_keys(self._settings, case_id, self._http)
        return llm_from_keys(self._settings, keys)


async def fetch_case_keys(
    settings: Settings, case_id: uuid.UUID, http: httpx.AsyncClient | None = None
) -> UserKeys:
    assert settings.internal_secret is not None
    url = f"{settings.api_internal_url.rstrip('/')}/internal/keys/{case_id}"
    headers = {"X-Internal-Secret": settings.internal_secret.get_secret_value()}
    if http is None:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
    else:
        r = await http.get(url, headers=headers)
    if r.status_code == 404:
        raise MissingUserKeys
    r.raise_for_status()  # API down or misconfigured: an ordinary error, retried by Temporal
    body = r.json()
    return UserKeys(anthropic=body.get("anthropic"), openai=body.get("openai"))
