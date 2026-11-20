"""/health tests. No network: the Temporal check is overridden or pointed at a closed port."""

from fastapi.testclient import TestClient

from recourse.api.health import TemporalHealth, check_temporal, temporal_health
from recourse.api.main import app
from recourse.config import Settings


def _client_with(temporal: TemporalHealth) -> TestClient:
    app.dependency_overrides[temporal_health] = lambda: temporal
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_health_when_temporal_reachable() -> None:
    client = _client_with(
        TemporalHealth(reachable=True, address="localhost:7233", namespace="default")
    )
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["api"] == "ok"
    assert body["temporal"]["reachable"] is True
    assert body["temporal"]["error"] is None


def test_health_when_temporal_down_still_200() -> None:
    client = _client_with(
        TemporalHealth(
            reachable=False, address="localhost:7233", namespace="default", error="RuntimeError"
        )
    )
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["api"] == "ok"
    assert r.json()["temporal"]["reachable"] is False


def test_cors_allows_frontend_origin() -> None:
    client = _client_with(TemporalHealth(reachable=True, address="x", namespace="default"))
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_check_temporal_unreachable_address() -> None:
    # Port 1 on localhost is closed: the check must fail fast and report, not raise.
    settings = Settings(_env_file=None, temporal_address="127.0.0.1:1")
    result = await check_temporal(settings)
    assert result.reachable is False
    assert result.error is not None


def test_public_config_has_only_safe_flags() -> None:
    from fastapi.testclient import TestClient

    from recourse.api.main import create_app
    from recourse.config import Settings, get_settings

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        email_sandbox=True, demo_mode=True, demo_day_seconds=10, sandbox_recipient="x@example.com"
    )
    body = TestClient(app).get("/config").json()
    assert body == {"email_sandbox": True, "demo_mode": True, "demo_day_seconds": 10}
