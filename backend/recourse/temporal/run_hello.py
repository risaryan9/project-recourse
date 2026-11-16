"""Start a HelloWorkflow and print its result (needs a running Temporal server and worker).

Run with: uv run python -m recourse.temporal.run_hello [name] [wait_days]
"""

import asyncio
import sys
import uuid

from temporalio.client import Client

from recourse.config import get_settings
from recourse.temporal.timeutil import TimeScale
from recourse.temporal.workflows import HelloInput, HelloWorkflow


async def main(name: str, wait_days: float) -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    workflow_id = f"hello-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        HelloWorkflow.run,
        HelloInput(name=name, wait_days=wait_days, scale=TimeScale.from_settings(settings)),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    print(f"Started workflow {workflow_id}")
    print(f"Result: {await handle.result()}")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main(args[0] if args else "Recourse", float(args[1]) if len(args) > 1 else 0))
