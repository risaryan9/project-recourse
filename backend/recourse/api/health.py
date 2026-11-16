"""Health endpoint: reports API status and whether the Temporal server is reachable."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from temporalio.client import Client

from recourse.config import Settings, get_settings

TEMPORAL_CHECK_TIMEOUT_S = 2.0

router = APIRouter(tags=["health"])


class TemporalHealth(BaseModel):
    reachable: bool
    address: str
    namespace: str
    error: str | None = None


class HealthResponse(BaseModel):
    api: Literal["ok"] = "ok"
    temporal: TemporalHealth


async def check_temporal(settings: Settings) -> TemporalHealth:
    """Connect to Temporal and run its gRPC health check, with a short timeout."""

    async def _check() -> None:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        await client.service_client.check_health()

    try:
        await asyncio.wait_for(_check(), timeout=TEMPORAL_CHECK_TIMEOUT_S)
        return TemporalHealth(
            reachable=True, address=settings.temporal_address, namespace=settings.temporal_namespace
        )
    except Exception as exc:  # any failure means "not reachable"; report it, don't crash
        return TemporalHealth(
            reachable=False,
            address=settings.temporal_address,
            namespace=settings.temporal_namespace,
            error=type(exc).__name__,
        )


async def temporal_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TemporalHealth:
    return await check_temporal(settings)


@router.get("/health")
async def health(
    temporal: Annotated[TemporalHealth, Depends(temporal_health)],
) -> HealthResponse:
    # Always 200 while the API itself is up; Temporal status is reported in the body.
    return HealthResponse(temporal=temporal)
