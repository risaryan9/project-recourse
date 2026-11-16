"""Temporal activities.

Side effects (LLM calls, DB writes, network) belong here, never in workflow code.
"""

from temporalio import activity
from temporalio.exceptions import ApplicationError


@activity.defn
async def say_hello(name: str) -> str:
    """Smoke-test activity used by HelloWorkflow."""
    if not name.strip():
        # Validation errors are not retryable: retrying would fail the same way.
        raise ApplicationError("name must not be empty", type="ValidationError", non_retryable=True)
    activity.logger.info("Saying hello to %s", name)
    return f"Hello, {name}!"
