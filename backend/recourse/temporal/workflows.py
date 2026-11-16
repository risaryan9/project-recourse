"""Temporal workflows. Orchestration only: deterministic and replay-safe.

No LLM/network calls, randomness or `datetime.now()` here. Use activities for side effects,
`workflow.now()` for time and `workflow.sleep()` (via `days()`) for timers.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from recourse.temporal.activities import say_hello
    from recourse.temporal.timeutil import TimeScale, days


@dataclass
class HelloInput:
    name: str
    wait_days: float = 0
    scale: TimeScale = field(default_factory=TimeScale)


@workflow.defn
class HelloWorkflow:
    """Smoke test: optionally waits `wait_days` (a durable timer), then calls one activity."""

    @workflow.run
    async def run(self, inp: HelloInput) -> str:
        if inp.wait_days > 0:
            await workflow.sleep(days(inp.wait_days, inp.scale))
        return await workflow.execute_activity(
            say_hello,
            inp.name,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(
                maximum_attempts=3, non_retryable_error_types=["ValidationError"]
            ),
        )
