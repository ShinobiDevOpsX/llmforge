"""Shared fixtures. Unit tests use FakeProvider and never touch the network."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ModelInfo,
)

TEST_API_KEY = "test-key-0123456789abcdef"
DEFAULT_MODEL = "llama3.2:1b"


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self) -> None:
        self.models = [ModelInfo(name=DEFAULT_MODEL, size_bytes=1_300_000_000, family="llama")]
        self.response_text = "Kubernetes orchestrates containers."
        self.prompt_tokens: int | None = 10
        self.completion_tokens: int | None = 5
        self.delay = 0.0
        self.error: Exception | None = None
        self.list_error: Exception | None = None
        self.requests: list[GenerationRequest] = []
        self.closed = False

    async def list_models(self) -> list[ModelInfo]:
        if self.list_error:
            raise self.list_error
        return list(self.models)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return GenerationResult(
            model=request.model,
            text=self.response_text,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            finish_reason="stop",
        )

    async def close(self) -> None:
        self.closed = True


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "api_keys": TEST_API_KEY,
        "default_model": DEFAULT_MODEL,
        "model_probe_interval_seconds": 0,
        "log_level": "WARNING",
        "rate_limit_requests_per_minute": 1000,
        "rate_limit_burst": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(fake_provider: FakeProvider) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(**overrides: object) -> TestClient:
        client = TestClient(create_app(build_settings(**overrides), provider=fake_provider))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}
