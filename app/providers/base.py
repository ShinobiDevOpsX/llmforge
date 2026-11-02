"""Provider-agnostic interface for model runtimes.

API routes and services depend only on this module. Adding a new runtime
(vLLM, a hosted API, ...) means implementing ``LLMProvider`` and registering
it in ``app.providers.registry`` - no endpoint changes required.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelInfo:
    name: str
    size_bytes: int | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    model: str
    prompt: str
    max_tokens: int
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    model: str
    text: str
    # Token counts are optional: not every runtime reports them, and Ollama
    # omits prompt_eval_count when the prompt is served from its cache.
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None = None


class ProviderError(Exception):
    """Base class for runtime failures. Messages are safe to show to clients."""

    error_type = "provider_error"

    def __init__(self, message: str = "The model runtime returned an error.") -> None:
        super().__init__(message)
        self.message = message


class ProviderUnavailableError(ProviderError):
    error_type = "runtime_unavailable"

    def __init__(self, message: str = "The model runtime is unreachable.") -> None:
        super().__init__(message)


class ProviderTimeoutError(ProviderError):
    error_type = "timeout"

    def __init__(self, message: str = "The model runtime did not respond in time.") -> None:
        super().__init__(message)


class ModelNotFoundError(ProviderError):
    error_type = "model_not_found"

    def __init__(self, model: str) -> None:
        super().__init__(f"Model '{model}' is not available in the runtime.")
        self.model = model


class LLMProvider(ABC):
    """A model runtime that can list models and generate completions."""

    name: str

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return models currently available in the runtime."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Produce a single, non-streamed completion."""

    async def is_model_available(self, model: str) -> bool:
        models = await self.list_models()
        wanted = normalize_model_name(model)
        return any(normalize_model_name(m.name) == wanted for m in models)

    async def close(self) -> None:  # noqa: B027 - optional hook, default no-op
        """Release network resources."""


def normalize_model_name(name: str) -> str:
    """Ollama treats ``llama3.2`` and ``llama3.2:latest`` as the same model."""
    return name if ":" in name else f"{name}:latest"
