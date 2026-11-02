"""Generation orchestration: validation, admission control, timeouts, metrics."""

from __future__ import annotations

import asyncio
import logging
import time

from app.config import Settings
from app.metrics import (
    ACTIVE_GENERATIONS,
    GENERATION_ERRORS,
    INFERENCE_DURATION,
    INPUT_TOKENS,
    MODEL_AVAILABILITY,
    OUTPUT_TOKENS,
)
from app.providers.base import (
    GenerationRequest,
    LLMProvider,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.schemas.generation import GenerateRequest, GenerateResponse
from app.services.errors import (
    InvalidParameterError,
    ModelNotAllowedError,
    OverloadedError,
    PromptTooLongError,
)

logger = logging.getLogger(__name__)

GENERATION_ERROR_TYPES = (
    ProviderError.error_type,
    ProviderUnavailableError.error_type,
    ProviderTimeoutError.error_type,
    ModelNotFoundError.error_type,
    "overloaded",
)


class GenerationService:
    def __init__(self, provider: LLMProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self._slots = asyncio.Semaphore(settings.max_concurrent_generations)
        self._initialize_metrics()

    def _initialize_metrics(self) -> None:
        """Export zero-valued series for every bounded label combination.

        Without this, rate()/increase() over a counter that has never been
        incremented returns no data instead of 0, leaving dashboards and
        error-ratio alerts blank until the first error occurs.
        """
        name = self.provider.name
        ACTIVE_GENERATIONS.labels(name)
        for model in self.settings.allowed_models:
            INPUT_TOKENS.labels(name, model)
            OUTPUT_TOKENS.labels(name, model)
            for error_type in GENERATION_ERROR_TYPES:
                GENERATION_ERRORS.labels(name, model, error_type)

    def resolve_model(self, requested: str | None) -> str:
        model = requested or self.settings.default_model
        if model not in self.settings.allowed_models:
            raise ModelNotAllowedError(
                f"Model '{model}' is not allowed. Allowed models: {', '.join(self.settings.allowed_models)}."
            )
        return model

    def _validate(self, request: GenerateRequest) -> int:
        if len(request.prompt) > self.settings.max_prompt_chars:
            raise PromptTooLongError(
                f"Prompt exceeds the maximum length of {self.settings.max_prompt_chars} characters."
            )
        max_tokens = request.max_tokens or self.settings.default_max_tokens
        if max_tokens > self.settings.max_output_tokens:
            raise InvalidParameterError(f"max_tokens must not exceed {self.settings.max_output_tokens}.")
        return max_tokens

    async def _acquire_slot(self) -> None:
        timeout = self.settings.queue_timeout_seconds
        if timeout == 0:
            if self._slots.locked():
                raise OverloadedError("All generation slots are busy.", retry_after=5)
            await self._slots.acquire()
            return
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=timeout)
        except TimeoutError:
            raise OverloadedError(
                "All generation slots are busy; try again later.", retry_after=max(1, int(timeout))
            ) from None

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        model = self.resolve_model(request.model)
        max_tokens = self._validate(request)
        provider = self.provider.name

        try:
            await self._acquire_slot()
        except OverloadedError:
            GENERATION_ERRORS.labels(provider, model, "overloaded").inc()
            raise

        ACTIVE_GENERATIONS.labels(provider).inc()
        started = time.perf_counter()
        outcome = "success"
        try:
            async with asyncio.timeout(self.settings.generation_timeout_seconds):
                result = await self.provider.generate(
                    GenerationRequest(
                        model=model,
                        prompt=request.prompt,
                        max_tokens=max_tokens,
                        temperature=request.temperature,
                    )
                )
        except TimeoutError:
            outcome = "error"
            GENERATION_ERRORS.labels(provider, model, ProviderTimeoutError.error_type).inc()
            raise ProviderTimeoutError() from None
        except ProviderError as exc:
            outcome = "error"
            GENERATION_ERRORS.labels(provider, model, exc.error_type).inc()
            if isinstance(exc, ModelNotFoundError):
                MODEL_AVAILABILITY.labels(provider, model).set(0)
            raise
        finally:
            duration = time.perf_counter() - started
            INFERENCE_DURATION.labels(provider, model, outcome).observe(duration)
            ACTIVE_GENERATIONS.labels(provider).dec()
            self._slots.release()

        if result.prompt_tokens is not None:
            INPUT_TOKENS.labels(provider, model).inc(result.prompt_tokens)
        if result.completion_tokens is not None:
            OUTPUT_TOKENS.labels(provider, model).inc(result.completion_tokens)

        logger.info(
            "generation completed",
            extra={
                "model": model,
                "prompt_chars": len(request.prompt),  # length only - prompts are never logged
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "finish_reason": result.finish_reason,
                "inference_seconds": round(duration, 3),
            },
        )
        return GenerateResponse(
            model=model,
            response=result.text,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            duration_seconds=round(duration, 3),
        )
