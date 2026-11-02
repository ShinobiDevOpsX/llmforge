"""FastAPI dependencies: shared state accessors, authentication, rate limiting."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.metrics import RATE_LIMITED_TOTAL
from app.providers.base import LLMProvider
from app.services.errors import AuthenticationError, RateLimitedError
from app.services.generation import GenerationService
from app.services.model_monitor import ModelAvailabilityMonitor
from app.services.rate_limiter import TokenBucketRateLimiter

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_provider(request: Request) -> LLMProvider:
    return request.app.state.provider


def get_generation_service(request: Request) -> GenerationService:
    return request.app.state.generation_service


def get_model_monitor(request: Request) -> ModelAvailabilityMonitor:
    return request.app.state.model_monitor


def get_rate_limiter(request: Request) -> TokenBucketRateLimiter | None:
    return request.app.state.rate_limiter


async def authenticate(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    api_key: Annotated[str | None, Depends(api_key_header)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    """Return a stable, non-reversible client identity used for rate limiting."""
    if not settings.auth_enabled:
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    presented = api_key or (bearer.credentials if bearer else None)
    if not presented:
        raise AuthenticationError("Missing API key. Send it in the X-API-Key header.")

    # Compare against every key in constant time so timing does not reveal
    # which key (or how much of it) matched.
    matched = False
    for key in settings.api_key_list:
        matched |= hmac.compare_digest(presented.encode(), key.encode())
    if not matched:
        raise AuthenticationError("Invalid API key.")
    return "key:" + hashlib.sha256(presented.encode()).hexdigest()[:16]


async def enforce_rate_limit(
    client_id: Annotated[str, Depends(authenticate)],
    limiter: Annotated[TokenBucketRateLimiter | None, Depends(get_rate_limiter)],
) -> str:
    if limiter is not None:
        allowed, retry_after = limiter.acquire(client_id)
        if not allowed:
            RATE_LIMITED_TOTAL.inc()
            raise RateLimitedError("Rate limit exceeded.", retry_after=retry_after)
    return client_id
