from datetime import timedelta

import pytest

from recourse.config import Settings
from recourse.temporal.timeutil import TimeScale, days


def test_real_days() -> None:
    assert days(1, TimeScale(demo_mode=False)) == timedelta(days=1)
    assert days(30, TimeScale(demo_mode=False, demo_day_seconds=5)) == timedelta(days=30)


def test_demo_days() -> None:
    scale = TimeScale(demo_mode=True, demo_day_seconds=60)
    assert days(1, scale) == timedelta(minutes=1)
    assert days(90, scale) == timedelta(minutes=90)
    assert days(0.5, scale) == timedelta(seconds=30)


def test_zero_days() -> None:
    assert days(0, TimeScale(demo_mode=True)) == timedelta(0)


def test_negative_days_rejected() -> None:
    with pytest.raises(ValueError):
        days(-1, TimeScale())


def test_non_positive_demo_day_seconds_rejected() -> None:
    with pytest.raises(ValueError):
        TimeScale(demo_mode=True, demo_day_seconds=0)


def test_from_settings() -> None:
    s = Settings(_env_file=None, demo_mode=True, demo_day_seconds=7)
    assert TimeScale.from_settings(s) == TimeScale(demo_mode=True, demo_day_seconds=7)
