"""Temporal worker process: polls the task queue and runs workflows and activities.

Run with: uv run python -m recourse.temporal.worker
"""

import asyncio
import logging

from temporalio.worker import Worker

from recourse.config import get_settings
from recourse.db.session import make_engine, make_session_factory
from recourse.llm import get_llm
from recourse.temporal.activities import say_hello
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_runner import connect
from recourse.temporal.case_workflow import CaseWorkflow
from recourse.temporal.workflows import HelloWorkflow

WORKFLOWS = [HelloWorkflow, CaseWorkflow]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    client = await connect(settings)
    engine = make_engine(settings=settings)
    cases = CaseActivities(make_session_factory(engine), get_llm(settings))
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=WORKFLOWS,
        activities=[say_hello, *cases.all()],
    )
    logging.info(
        "Worker started: address=%s namespace=%s task_queue=%s",
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_task_queue,
    )
    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
