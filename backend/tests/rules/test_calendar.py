"""Approximate bank working-day calendar (see recourse/rules/calendar.py)."""

from datetime import date

import pytest

from recourse.rules.calendar import (
    add_calendar_days,
    add_working_days,
    is_working_day,
    working_days_after,
)


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 11, 2), True),  # Monday
        (date(2026, 11, 1), False),  # Sunday
        (date(2026, 11, 7), True),  # 1st Saturday
        (date(2026, 11, 14), False),  # 2nd Saturday
        (date(2026, 11, 21), True),  # 3rd Saturday
        (date(2026, 11, 28), False),  # 4th Saturday
        (date(2026, 10, 31), True),  # 5th Saturday
        (date(2026, 1, 26), False),  # Republic Day (Monday)
        (date(2026, 8, 15), False),  # Independence Day (3rd Saturday)
        (date(2026, 10, 2), False),  # Gandhi Jayanti (Friday)
    ],
)
def test_is_working_day(d: date, expected: bool) -> None:
    assert is_working_day(d) is expected


def test_working_days_after_excludes_start_day() -> None:
    assert working_days_after(date(2026, 11, 2), date(2026, 11, 2)) == 0
    assert working_days_after(date(2026, 11, 2), date(2026, 11, 3)) == 1
    assert working_days_after(date(2026, 11, 5), date(2026, 11, 2)) == 0  # end before start


def test_working_days_skip_weekend_and_2nd_saturday() -> None:
    # Fri 13 Nov 2026 -> Sat 14 (2nd Sat) off, Sun 15 off, Mon 16 is day 1.
    assert add_working_days(date(2026, 11, 13), 1) == date(2026, 11, 16)
    assert working_days_after(date(2026, 11, 13), date(2026, 11, 18)) == 3


def test_add_working_days_requires_positive_n() -> None:
    with pytest.raises(ValueError):
        add_working_days(date(2026, 11, 2), 0)


def test_add_and_count_are_inverse() -> None:
    start = date(2026, 1, 1)
    for n in range(1, 40):
        assert working_days_after(start, add_working_days(start, n)) == n


def test_add_calendar_days() -> None:
    assert add_calendar_days(date(2026, 12, 29), 5) == date(2027, 1, 3)
    assert add_calendar_days(date(2028, 2, 28), 1) == date(2028, 2, 29)  # leap year
    with pytest.raises(ValueError):
        add_calendar_days(date(2026, 1, 1), -1)
