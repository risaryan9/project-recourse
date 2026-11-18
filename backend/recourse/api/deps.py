"""Shared API dependencies: database sessions and the CaseWorkflow runner.

Both are created lazily on first use (so `/health` works without a database or Temporal) and
cached on `app.state`. Tests replace them with `app.dependency_overrides`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recourse.config import get_settings
from recourse.db.session import make_engine, make_session_factory
from recourse.temporal.case_runner import CaseRunner, TemporalCaseRunner, connect


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] | None = getattr(request.app.state, "sessions", None)
    if factory is None:
        try:
            factory = make_session_factory(make_engine())
        except RuntimeError as e:
            raise HTTPException(503, "database is not configured") from e
        request.app.state.sessions = factory
    return factory


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


async def get_runner(request: Request) -> CaseRunner:
    runner: CaseRunner | None = getattr(request.app.state, "runner", None)
    if runner is None:
        settings = get_settings()
        try:
            client = await connect(settings)
        except Exception as e:  # connection refused, DNS, TLS...
            raise HTTPException(503, "case engine (Temporal) is not reachable") from e
        runner = TemporalCaseRunner(client, settings.temporal_task_queue)
        request.app.state.runner = runner
    return runner
