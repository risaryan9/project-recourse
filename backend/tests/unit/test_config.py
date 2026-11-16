import pytest

from recourse.config import Settings


def test_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("TEMPORAL_ADDRESS", "DEMO_MODE", "DEMO_DAY_SECONDS", "ANTHROPIC_MAIN_MODEL"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.temporal_address == "localhost:7233"
    assert s.temporal_task_queue == "recourse-cases"
    assert s.demo_mode is False
    assert s.demo_day_seconds == 60


def test_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_DAY_SECONDS", "5")
    monkeypatch.setenv("ANTHROPIC_MAIN_MODEL", "some-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.demo_mode is True
    assert s.demo_day_seconds == 5
    assert s.anthropic_main_model == "some-model"
    # Secrets never appear in repr/logs.
    assert "sk-test" not in repr(s)


def test_demo_day_seconds_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_DAY_SECONDS", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]
