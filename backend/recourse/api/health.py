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


class PublicConfig(BaseModel):
    """What the UI needs to label itself honestly. No secrets, no addresses."""

    email_sandbox: bool  # True: show "Sandbox: not sent to the real bank"
    demo_mode: bool  # True: one "day" lasts demo_day_seconds
    demo_day_seconds: int
    require_user_keys: bool  # True: each visitor runs cases on their own AI key (ADR-023)


@router.get("/config")
async def public_config(settings: Annotated[Settings, Depends(get_settings)]) -> PublicConfig:
    return PublicConfig(
        email_sandbox=settings.email_sandbox,
        demo_mode=settings.demo_mode,
        demo_day_seconds=settings.demo_day_seconds,
        require_user_keys=settings.require_user_keys,
    )
