"""API -> Ollama integration tests. Require the running compose stack."""

from __future__ import annotations

import re
import socket

import pytest

from app.providers.base import GenerationRequest, ProviderUnavailableError
from app.providers.ollama import OllamaProvider
from tests.conftest import build_settings

pytestmark = pytest.mark.integration


def _metric_total(text: str, name: str) -> float:
    pattern = re.compile(rf"^{name}(?:\{{[^}}]*\}})? ([0-9.e+-]+)$", re.MULTILINE)
    return sum(float(v) for v in pattern.findall(text))


def test_ready_reports_model_available(live_client):
    response = live_client.get("/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_reachable"] is True
    assert body["model_available"] is True


def test_models_lists_default_model(live_client):
    response = live_client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "ollama"
    names = [m["name"] for m in body["models"]]
    assert body["default_model"] in names


def test_generation_returns_completion_and_updates_metrics(live_client):
    before = live_client.get("/metrics").text
    response = live_client.post(
        "/v1/generate",
        json={"prompt": "Reply with the single word: pong", "max_tokens": 16, "temperature": 0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response"].strip()
    assert body["completion_tokens"] is not None
    assert body["completion_tokens"] > 0
    assert body["duration_seconds"] > 0

    after = live_client.get("/metrics").text
    assert _metric_total(after, "llmforge_output_tokens_total") > _metric_total(
        before, "llmforge_output_tokens_total"
    )
    assert _metric_total(after, "llmforge_inference_duration_seconds_count") > _metric_total(
        before, "llmforge_inference_duration_seconds_count"
    )


def test_live_validation_and_auth(live_client, api_url):
    import httpx

    assert live_client.post("/v1/generate", json={"prompt": ""}).status_code == 422
    assert live_client.post("/v1/generate", json={"prompt": "hi", "model": "nope:1b"}).status_code == 400
    assert httpx.post(f"{api_url}/v1/generate", json={"prompt": "hi"}).status_code == 401


def _unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_runtime_unavailable_is_reported():
    """Real OllamaProvider pointed at a closed port: no mocks involved."""
    provider = OllamaProvider(
        f"http://127.0.0.1:{_unused_port()}", connect_timeout=1, generation_timeout=2, probe_timeout=1
    )
    try:
        with pytest.raises(ProviderUnavailableError):
            await provider.list_models()
        with pytest.raises(ProviderUnavailableError):
            await provider.generate(GenerationRequest(model="llama3.2:1b", prompt="hi", max_tokens=1))
    finally:
        await provider.close()


def test_gateway_reports_not_ready_when_runtime_down():
    """Full app with the real Ollama provider and an unreachable runtime."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = build_settings(ollama_base_url=f"http://127.0.0.1:{_unused_port()}", connect_timeout_seconds=1)
    with TestClient(create_app(settings)) as client:
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["runtime_reachable"] is False
        headers = {"X-API-Key": settings.api_key_list[0]}
        gen = client.post("/v1/generate", json={"prompt": "hi"}, headers=headers)
        assert gen.status_code == 503
        assert gen.json()["error"]["code"] == "runtime_unavailable"
