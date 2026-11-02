"""Operational endpoints: liveness, readiness, Prometheus metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import __version__
from app.config import Settings
from app.dependencies import get_model_monitor, get_settings
from app.schemas.health import HealthResponse, ReadinessResponse
from app.services.model_monitor import ModelAvailabilityMonitor

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Returns 200 while the process is able to serve requests. Does not touch the runtime."""
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Runtime or model unavailable"}},
    summary="Readiness probe",
)
async def ready(
    settings: Annotated[Settings, Depends(get_settings)],
    monitor: Annotated[ModelAvailabilityMonitor, Depends(get_model_monitor)],
) -> JSONResponse:
    """Returns 200 only when the runtime is reachable and the default model is pulled."""
    status = await monitor.check()
    is_ready = status.runtime_reachable and status.model_available
    body = ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        provider=settings.provider,
        model=settings.default_model,
        runtime_reachable=status.runtime_reachable,
        model_available=status.model_available,
        detail=status.detail,
    )
    return JSONResponse(body.model_dump(), status_code=200 if is_ready else 503)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
