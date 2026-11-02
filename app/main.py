"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app import __version__
from app.api import health, v1
from app.api.errors import register_exception_handlers
from app.config import Settings, get_settings
from app.logging import configure_logging
from app.metrics import initialize_request_series
from app.middleware import RequestContextMiddleware
from app.providers.base import LLMProvider
from app.providers.registry import create_provider
from app.services.generation import GenerationService
from app.services.model_monitor import ModelAvailabilityMonitor
from app.services.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``provider`` can be injected (tests do this with a fake); otherwise the
    provider named by ``LLMFORGE_PROVIDER`` is created from the registry.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    provider = provider or create_provider(settings)
    monitor = ModelAvailabilityMonitor(provider, settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting llmforge",
            extra={
                "version": __version__,
                "environment": settings.environment,
                "provider": provider.name,
                "default_model": settings.default_model,
                "auth_enabled": settings.auth_enabled,
                "rate_limit_enabled": settings.rate_limit_enabled,
            },
        )
        probe: asyncio.Task[None] | None = None
        if settings.model_probe_interval_seconds > 0:
            probe = asyncio.create_task(monitor.run_forever(), name="model-availability-probe")
        try:
            yield
        finally:
            if probe:
                probe.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await probe
            await provider.close()
            logger.info("llmforge stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="LLMOps gateway for locally hosted language models.",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.provider = provider
    app.state.model_monitor = monitor
    app.state.generation_service = GenerationService(provider, settings)
    app.state.rate_limiter = (
        TokenBucketRateLimiter(settings.rate_limit_requests_per_minute, settings.rate_limit_burst)
        if settings.rate_limit_enabled
        else None
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(v1.router)
    initialize_request_series(
        [
            (method, route.path)
            for route in v1.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        ]
    )
    app.add_middleware(RequestContextMiddleware, max_body_bytes=settings.max_request_body_bytes)
    return app
