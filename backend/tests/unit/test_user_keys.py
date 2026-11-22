"""Bring your own key (ADR-023): keys live only in memory, never leak, and gate model work."""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from temporalio.exceptions import ApplicationError

from recourse.api import keys as keys_api
from recourse.api.deps import get_session
from recourse.api.main import app
from recourse.config import Settings, get_settings
from recourse.llm import user_keys as uk
from recourse.temporal.case_activities import NEEDS_API_KEY, CaseActivities

SECRET = "internal-test-secret"
ANT = "sk-ant-test-0000000000"
OAI = "sk-test-1111111111"


def _settings(**kw: object) -> Settings:
    base = {"require_user_keys": True, "internal_secret": SECRET, "anthropic_api_key": None}
    return Settings(_env_file=None, **(base | kw))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _fresh_vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(uk, "_vault", None)
    yield
    app.dependency_overrides.clear()


# --- the vault -------------------------------------------------------------------------------


def test_vault_forgets_after_ttl() -> None:
    now = [0.0]
    vault = uk.KeyVault(60, clock=lambda: now[0])
    vault.put("owner", uk.UserKeys(anthropic=ANT))
    assert vault.get("owner") is not None
    assert vault.expires_in("owner") == 60
    now[0] = 61
    assert vault.get("owner") is None


def test_vault_forget_and_owners_are_separate() -> None:
    vault = uk.KeyVault(60)
    vault.put("a", uk.UserKeys(anthropic=ANT))
    assert vault.get("b") is None
    vault.forget("a")
    assert vault.get("a") is None


def test_keys_never_print() -> None:
    keys = uk.UserKeys(anthropic=ANT, openai=OAI)
    for text in (repr(keys), str(keys), f"{keys}"):
        assert ANT not in text and OAI not in text


def test_clean_key_rejects_without_echoing_the_value() -> None:
    with pytest.raises(ValueError) as e:
        uk.clean_key("not-a-key-SECRET123", uk.ANTHROPIC_PREFIX)
    assert "SECRET123" not in str(e.value)
    assert uk.clean_key("  " + ANT + " ", uk.ANTHROPIC_PREFIX) == ANT
    assert uk.clean_key("", uk.ANTHROPIC_PREFIX) is None


def test_llm_uses_only_the_given_keys() -> None:
    s = _settings(openai_api_key="sk-server-key-should-not-be-used")
    assert uk.llm_from_keys(s, uk.UserKeys(anthropic=ANT)).provider_names == ["anthropic"]  # type: ignore[attr-defined]
    with pytest.raises(uk.MissingUserKeys):
        uk.llm_from_keys(s, uk.UserKeys())


# --- the API ---------------------------------------------------------------------------------


def test_actions_need_a_key_when_required() -> None:
    s = _settings()
    with pytest.raises(HTTPException) as e:
        keys_api.require_user_keys(None, s)
    assert e.value.status_code == 428
    uk.get_vault(s).put("owner", uk.UserKeys(anthropic=ANT))
    keys_api.require_user_keys("owner", s)  # no error
    keys_api.require_user_keys(None, _settings(require_user_keys=False))  # not required: fine


def test_save_checks_then_holds_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings()
    app.dependency_overrides[get_settings] = lambda: s

    async def ok(_s: Settings, keys: uk.UserKeys) -> dict[str, str]:
        return {"anthropic": "ok"}

    monkeypatch.setattr(keys_api, "check_keys", ok)
    client = TestClient(app, base_url="https://testserver")
    assert client.get("/keys").json()["anthropic"] is False
    r = client.put("/keys", json={"anthropic_key": ANT})
    assert r.status_code == 200 and r.json()["anthropic"] is True
    assert ANT not in r.text
    assert client.get("/keys").json()["anthropic"] is True  # same browser (owner cookie)
    assert client.delete("/keys").json()["anthropic"] is False


def test_rejected_key_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings()
    app.dependency_overrides[get_settings] = lambda: s

    async def bad(_s: Settings, keys: uk.UserKeys) -> dict[str, str]:
        return {"anthropic": "Anthropic rejected this key."}

    monkeypatch.setattr(keys_api, "check_keys", bad)
    client = TestClient(app, base_url="https://testserver")
    r = client.put("/keys", json={"anthropic_key": ANT})
    assert r.status_code == 400 and ANT not in r.text
    assert client.get("/keys").json()["anthropic"] is False


def _internal_client(owner: str) -> TestClient:
    s = _settings()
    app.dependency_overrides[get_settings] = lambda: s
    uk.get_vault(s).put(owner, uk.UserKeys(anthropic=ANT))

    class FakeSession:
        async def get(self, _model: object, _id: object) -> object:
            return SimpleNamespace(owner_id=owner)

    async def session():  # type: ignore[no-untyped-def]
        yield FakeSession()

    app.dependency_overrides[get_session] = session
    return TestClient(app, base_url="https://testserver")


def test_internal_lookup_needs_the_secret() -> None:
    client = _internal_client("owner")
    url = f"/internal/keys/{uuid.uuid4()}"
    assert client.get(url).status_code == 404
    assert client.get(url, headers={"X-Internal-Secret": "wrong"}).status_code == 404
    r = client.get(url, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200 and r.json()["anthropic"] == ANT


def test_internal_lookup_refuses_anything_through_a_proxy() -> None:
    client = _internal_client("owner")
    r = client.get(
        f"/internal/keys/{uuid.uuid4()}",
        headers={"X-Internal-Secret": SECRET, "X-Forwarded-For": "203.0.113.9"},
    )
    assert r.status_code == 404


# --- the worker ------------------------------------------------------------------------------


async def test_fetch_case_keys() -> None:
    s = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-internal-secret"] == SECRET
        if request.url.path.endswith(str(uuid.UUID(int=2))):
            return httpx.Response(404)
        return httpx.Response(200, json={"anthropic": ANT, "openai": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        keys = await uk.fetch_case_keys(s, uuid.UUID(int=1), http)
        assert keys.anthropic == ANT and keys.openai is None
        with pytest.raises(uk.MissingUserKeys):
            await uk.fetch_case_keys(s, uuid.UUID(int=2), http)


async def test_missing_key_fails_the_step_clearly() -> None:
    async def no_key(_case_id: uuid.UUID) -> object:
        raise uk.MissingUserKeys

    acts = CaseActivities(None, None, llm_for=no_key, settings=_settings())  # type: ignore[arg-type]
    with pytest.raises(ApplicationError) as e:
        await acts._model(uuid.uuid4())
    assert e.value.type == NEEDS_API_KEY and e.value.non_retryable
