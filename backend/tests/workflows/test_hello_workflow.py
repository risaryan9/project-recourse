"""HelloWorkflow tests on Temporal's time-skipping test server.

The test server fast-forwards time whenever all workflows are waiting on timers,
so a multi-day timer completes in milliseconds.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from recourse.temporal.activities import say_hello
from recourse.temporal.timeutil import TimeScale
from recourse.temporal.workflows import HelloInput, HelloWorkflow

TASK_QUEUE = "test-hello"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env() -> AsyncIterator[WorkflowEnvironment]:
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


async def _run(env: WorkflowEnvironment, inp: HelloInput) -> tuple[str, list[timedelta]]:
    """Run the workflow; return its result and the durations of the timers it started."""
    async with Worker(
        env.client, task_queue=TASK_QUEUE, workflows=[HelloWorkflow], activities=[say_hello]
    ):
        handle = await env.client.start_workflow(
            HelloWorkflow.run, inp, id=f"hello-{uuid.uuid4()}", task_queue=TASK_QUEUE
        )
        result = await handle.result()
        history = await handle.fetch_history()
    timers = [
        e.timer_started_event_attributes.start_to_fire_timeout.ToTimedelta()
        for e in history.events
        if e.HasField("timer_started_event_attributes")
    ]
    return result, timers


@pytest.mark.asyncio(loop_scope="module")
async def test_hello_returns_greeting(env: WorkflowEnvironment) -> None:
    result, timers = await _run(env, HelloInput(name="Recourse"))
    assert result == "Hello, Recourse!"
    assert timers == []


@pytest.mark.asyncio(loop_scope="module")
async def test_hello_timer_uses_real_days(env: WorkflowEnvironment) -> None:
    # A 30-day durable timer, completed instantly thanks to time skipping.
    result, timers = await _run(env, HelloInput(name="Timer", wait_days=30, scale=TimeScale()))
    assert result == "Hello, Timer!"
    assert timers == [timedelta(days=30)]


@pytest.mark.asyncio(loop_scope="module")
async def test_hello_timer_respects_demo_mode(env: WorkflowEnvironment) -> None:
    scale = TimeScale(demo_mode=True, demo_day_seconds=60)
    _, timers = await _run(env, HelloInput(name="Demo", wait_days=30, scale=scale))
    # 30 demo "days" = 30 minutes.
    assert timers == [timedelta(minutes=30)]


@pytest.mark.asyncio(loop_scope="module")
async def test_empty_name_fails_without_retry(env: WorkflowEnvironment) -> None:
    with pytest.raises(WorkflowFailureError):
        await _run(env, HelloInput(name="  "))
