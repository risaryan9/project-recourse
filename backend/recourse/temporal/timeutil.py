"""Single helper for every timer duration in the system.

In DEMO_MODE one "day" is shortened to DEMO_DAY_SECONDS so a weeks-long case can be
demonstrated in minutes.

Determinism note: workflow code must not read env vars or settings (a replay on another
worker could see different values). So the scale is captured once, outside the workflow,
as a `TimeScale` and passed in as workflow input. It is then part of the workflow history.
"""

from dataclasses import dataclass
from datetime import timedelta

from recourse.config import Settings

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class TimeScale:
    demo_mode: bool = False
    demo_day_seconds: int = 60

    def __post_init__(self) -> None:
        if self.demo_day_seconds <= 0:
            raise ValueError("demo_day_seconds must be positive")

    @classmethod
    def from_settings(cls, settings: Settings) -> "TimeScale":
        return cls(demo_mode=settings.demo_mode, demo_day_seconds=settings.demo_day_seconds)


def days(n: float, scale: TimeScale) -> timedelta:
    """Duration of `n` days: real days normally, `n * demo_day_seconds` seconds in demo mode."""
    if n < 0:
        raise ValueError("n must be non-negative")
    seconds_per_day = scale.demo_day_seconds if scale.demo_mode else SECONDS_PER_DAY
    return timedelta(seconds=n * seconds_per_day)
