"""How the API talks to CaseWorkflows: start one, send it signals, ask its state.

`CaseRunner` is the interface the API depends on; `TemporalCaseRunner` is the real one. API
tests use a fake, so they need no Temporal server.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Protocol

from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError

from recourse.config import Settings
from recourse.temporal.case_types import AbandonRequest, CaseInput, CaseState, Confirmation
from recourse.temporal.case_workflow import CaseWorkflow


def workflow_id(case_id: uuid.UUID) -> str:
    return f"case-{case_id}"


async def connect(settings: Settings) -> Client:
    """A Temporal client using the Pydantic data converter (worker and API must match)."""
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


class CaseRunner(Protocol):
    async def start(self, inp: CaseInput) -> None: ...
    async def confirm(self, case_id: uuid.UUID, confirmation: Confirmation) -> None: ...
    async def abandon(self, case_id: uuid.UUID, request: AbandonRequest) -> None: ...
    async def state(self, case_id: uuid.UUID) -> CaseState | None: ...


class TemporalCaseRunner:
    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    def _handle(self, case_id: uuid.UUID) -> WorkflowHandle[CaseWorkflow, object]:
        return self._client.get_workflow_handle_for(CaseWorkflow.run, workflow_id(case_id))

    async def start(self, inp: CaseInput) -> None:
        await self._client.start_workflow(
            CaseWorkflow.run, inp, id=workflow_id(inp.case_id), task_queue=self._task_queue
        )

    async def confirm(self, case_id: uuid.UUID, confirmation: Confirmation) -> None:
        await self._handle(case_id).signal(CaseWorkflow.confirm, confirmation)

    async def abandon(self, case_id: uuid.UUID, request: AbandonRequest) -> None:
        await self._handle(case_id).signal(CaseWorkflow.abandon, request)

    async def state(self, case_id: uuid.UUID) -> CaseState | None:
        """The live workflow state, or None if the workflow cannot be reached right now."""
        try:
            return await self._handle(case_id).query(
                CaseWorkflow.get_state, rpc_timeout=timedelta(seconds=3)
            )
        except (RPCError, TimeoutError):
            return None
