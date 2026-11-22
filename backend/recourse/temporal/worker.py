"""Temporal worker process: polls the task queue and runs workflows and activities.

Run with: uv run python -m recourse.temporal.worker
"""

import asyncio
import logging

from temporalio.worker import Worker

from recourse.config import get_settings
from recourse.db.session import make_engine, make_session_factory
from recourse.llm import get_llm
from recourse.llm.user_keys import CaseKeyResolver
from recourse.temporal.activities import say_hello
from recourse.temporal.case_activities import CaseActivities
from recourse.temporal.case_runner import connect
from recourse.temporal.case_workflow import CaseWorkflow
from recourse.temporal.workflows import HelloWorkflow
from recourse.tracing import configure_tracing
from recourse.tracing import flush as flush_traces

WORKFLOWS = [HelloWorkflow, CaseWorkflow]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    configure_tracing(settings)  # before any client is built, so model calls are wrapped
    client = await connect(settings)
    engine = make_engine(settings=settings)
    if settings.require_user_keys:
        # Bring your own key (ADR-023): never the server's keys; each case uses its owner's.
        cases = CaseActivities(
            make_session_factory(engine), None, llm_for=CaseKeyResolver(settings)
        )
    else:
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
        flush_traces()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
