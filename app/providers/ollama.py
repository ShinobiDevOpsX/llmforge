"""Ollama runtime provider (https://github.com/ollama/ollama/blob/main/docs/api.md)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.providers.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ModelInfo,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class _HTTPStatusError(ProviderError):
    """Non-2xx response from Ollama; the status code drives error mapping."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float,
        generation_timeout: float,
        probe_timeout: float,
        keep_alive: str = "10m",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._generation_timeout = httpx.Timeout(generation_timeout, connect=connect_timeout)
        self._probe_timeout = httpx.Timeout(probe_timeout, connect=connect_timeout)
        self._keep_alive = keep_alive
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def _request(self, method: str, path: str, *, request_timeout: httpx.Timeout, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, path, timeout=request_timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.TransportError as exc:
            logger.warning("ollama transport error", extra={"error": type(exc).__name__})
            raise ProviderUnavailableError() from exc

        if response.is_success:
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError("The model runtime returned an invalid response.") from exc

        # Ollama's error bodies look like {"error": "..."}; keep them in logs only.
        logger.warning(
            "ollama returned an error",
            extra={"status_code": response.status_code, "body": response.text[:500]},
        )
        if response.status_code >= 500:
            raise _HTTPStatusError(response.status_code, "The model runtime returned an error.")
        raise _HTTPStatusError(response.status_code, "The model runtime rejected the request.")

    async def list_models(self) -> list[ModelInfo]:
        data = await self._request("GET", "/api/tags", request_timeout=self._probe_timeout)
        models = []
        for item in data.get("models", []):
            details = item.get("details") or {}
            models.append(
                ModelInfo(
                    name=item.get("name") or item.get("model", ""),
                    size_bytes=item.get("size"),
                    family=details.get("family"),
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                )
            )
        return models

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        options: dict[str, Any] = {"num_predict": request.max_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": options,
        }
        try:
            data = await self._request(
                "POST", "/api/generate", json=payload, request_timeout=self._generation_timeout
            )
        except _HTTPStatusError as exc:
            if exc.status_code == 404:
                raise ModelNotFoundError(request.model) from exc
            raise
        return GenerationResult(
            model=data.get("model", request.model),
            text=data.get("response", ""),
            prompt_tokens=_optional_int(data.get("prompt_eval_count")),
            completion_tokens=_optional_int(data.get("eval_count")),
            finish_reason=data.get("done_reason"),
        )

    async def close(self) -> None:
        await self._client.aclose()


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
