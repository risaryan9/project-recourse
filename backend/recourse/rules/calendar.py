"""Day counting for the rule engine.

Conventions (recorded in docs/decisions.md, ADR-002):

* **"Within N days of date D"** excludes D itself (as in the General Clauses Act, 1897, s.9).
  So the last allowed day is D + N.
* **Calendar days** are plain dates.
* **Working days** (fraud Regime A) are officially counted "as per the working schedule of the
  home branch of the customer", which we cannot know. We APPROXIMATE it: every day except
  Sundays, the 2nd and 4th Saturdays of the month, and the three national holidays (26 Jan,
  15 Aug, 2 Oct). State and festival holidays are not included, so the true deadline can be
  later than ours, never earlier. Results are always flagged as approximate.
"""

from datetime import date, timedelta

# Fixed-date national holidays (month, day).
NATIONAL_HOLIDAYS: frozenset[tuple[int, int]] = frozenset({(1, 26), (8, 15), (10, 2)})

_SATURDAY = 5
_SUNDAY = 6


def is_working_day(d: date) -> bool:
    """Approximate bank working day (see module docstring)."""
    weekday = d.weekday()
    if weekday == _SUNDAY:
        return False
    if weekday == _SATURDAY:
        nth_saturday = (d.day - 1) // 7 + 1
        if nth_saturday in (2, 4):
            return False
    return (d.month, d.day) not in NATIONAL_HOLIDAYS


def working_days_after(start: date, end: date) -> int:
    """Number of working days in the interval (start, end], i.e. excluding `start`.

    Returns 0 if `end <= start`.
    """
    count = 0
    d = start + timedelta(days=1)
    while d <= end:
        if is_working_day(d):
            count += 1
        d += timedelta(days=1)
    return count


def add_working_days(start: date, n: int) -> date:
    """The n-th working day after `start` (excluding `start`). `n` must be >= 1."""
    if n < 1:
        raise ValueError("n must be at least 1")
    d = start
    remaining = n
    while remaining:
        d += timedelta(days=1)
        if is_working_day(d):
            remaining -= 1
    return d


def add_calendar_days(start: date, n: int) -> date:
    """`start` + n calendar days (the last day of a "within n days of start" window)."""
    if n < 0:
        raise ValueError("n must be non-negative")
    return start + timedelta(days=n)


def calendar_days_after(start: date, end: date) -> int:
    """Calendar days from `start` to `end` (negative if `end` is before `start`)."""
    return (end - start).days
