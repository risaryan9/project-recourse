"""Temporal worker process: polls the task queue and runs workflows and activities.

Run with: uv run python -m recourse.temporal.worker
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from recourse.config import get_settings
from recourse.temporal.activities import say_hello
from recourse.temporal.workflows import HelloWorkflow

WORKFLOWS = [HelloWorkflow]
ACTIVITIES = [say_hello]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
    )
    logging.info(
        "Worker started: address=%s namespace=%s task_queue=%s",
        settings.temporal_address,
        settings.temporal_namespace,
        settings.temporal_task_queue,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
