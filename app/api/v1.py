"""Versioned public API. Requires authentication and is rate limited."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.config import Settings
from app.dependencies import (
    enforce_rate_limit,
    get_generation_service,
    get_provider,
    get_settings,
)
from app.providers.base import LLMProvider
from app.schemas.errors import ErrorResponse
from app.schemas.generation import GenerateRequest, GenerateResponse
from app.schemas.models import ModelListResponse, ModelSummary
from app.services.generation import GenerationService

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    503: {"model": ErrorResponse, "description": "Runtime unavailable or overloaded"},
}

router = APIRouter(prefix="/v1", tags=["inference"], dependencies=[Depends(enforce_rate_limit)])


@router.get("/models", response_model=ModelListResponse, responses=_ERRORS, summary="List models")
async def list_models(
    settings: Annotated[Settings, Depends(get_settings)],
    provider: Annotated[LLMProvider, Depends(get_provider)],
    response: Response,
) -> ModelListResponse:
    """Models currently present in the runtime, flagged by whether the gateway allows them."""
    response.headers["Cache-Control"] = "no-store"
    models = await provider.list_models()
    allowed = set(settings.allowed_models)
    return ModelListResponse(
        provider=provider.name,
        default_model=settings.default_model,
        models=[
            ModelSummary(
                name=m.name,
                allowed=m.name in allowed,
                size_bytes=m.size_bytes,
                family=m.family,
                parameter_size=m.parameter_size,
                quantization=m.quantization,
            )
            for m in models
        ],
    )


@router.post(
    "/generate",
    response_model=GenerateResponse,
    responses={
        **_ERRORS,
        400: {"model": ErrorResponse, "description": "Model not allowed"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
        502: {"model": ErrorResponse, "description": "Runtime returned an error"},
        504: {"model": ErrorResponse, "description": "Generation timed out"},
    },
    summary="Generate a completion",
)
async def generate(
    body: GenerateRequest,
    service: Annotated[GenerationService, Depends(get_generation_service)],
    response: Response,
) -> GenerateResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.generate(body)
