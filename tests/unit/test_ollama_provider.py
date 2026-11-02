"""OllamaProvider against a mocked HTTP transport (respx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.providers.base import (
    GenerationRequest,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.ollama import OllamaProvider
from app.providers.registry import create_provider
from tests.conftest import build_settings

BASE = "http://ollama.test:11434"


@pytest.fixture
async def provider():
    p = OllamaProvider(BASE, connect_timeout=1, generation_timeout=5, probe_timeout=1, keep_alive="1m")
    yield p
    await p.close()


@respx.mock
async def test_list_models(provider):
    respx.get(f"{BASE}/api/tags").respond(
        json={
            "models": [
                {
                    "name": "llama3.2:1b",
                    "size": 1321098329,
                    "details": {"family": "llama", "parameter_size": "1.2B", "quantization_level": "Q8_0"},
                }
            ]
        }
    )
    models = await provider.list_models()
    assert models[0].name == "llama3.2:1b"
    assert models[0].parameter_size == "1.2B"
    assert models[0].quantization == "Q8_0"


@respx.mock
async def test_is_model_available_normalizes_tags(provider):
    respx.get(f"{BASE}/api/tags").respond(json={"models": [{"name": "phi3:latest"}]})
    assert await provider.is_model_available("phi3") is True
    assert await provider.is_model_available("phi3:mini") is False


@respx.mock
async def test_generate_maps_response_and_payload(provider):
    route = respx.post(f"{BASE}/api/generate").respond(
        json={
            "model": "llama3.2:1b",
            "response": "Hello!",
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 26,
            "eval_count": 3,
        }
    )
    result = await provider.generate(
        GenerationRequest(model="llama3.2:1b", prompt="Hi", max_tokens=16, temperature=0.1)
    )
    assert result.text == "Hello!"
    assert result.prompt_tokens == 26
    assert result.completion_tokens == 3
    assert result.finish_reason == "stop"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "model": "llama3.2:1b",
        "prompt": "Hi",
        "stream": False,
        "keep_alive": "1m",
        "options": {"num_predict": 16, "temperature": 0.1},
    }


@respx.mock
async def test_generate_handles_missing_token_counts(provider):
    respx.post(f"{BASE}/api/generate").respond(json={"response": "ok", "done": True})
    result = await provider.generate(GenerationRequest(model="m:1", prompt="p", max_tokens=1))
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


@respx.mock
async def test_generate_404_is_model_not_found(provider):
    respx.post(f"{BASE}/api/generate").respond(404, json={"error": "model 'x' not found"})
    with pytest.raises(ModelNotFoundError):
        await provider.generate(GenerationRequest(model="x:1", prompt="p", max_tokens=1))


@respx.mock
async def test_server_error_is_provider_error(provider):
    respx.post(f"{BASE}/api/generate").respond(500, json={"error": "out of memory at 0xdeadbeef"})
    with pytest.raises(ProviderError) as info:
        await provider.generate(GenerationRequest(model="m:1", prompt="p", max_tokens=1))
    assert "0xdeadbeef" not in info.value.message


@respx.mock
async def test_connection_error_is_unavailable(provider):
    respx.get(f"{BASE}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailableError):
        await provider.list_models()


@respx.mock
async def test_timeout_is_provider_timeout(provider):
    respx.post(f"{BASE}/api/generate").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(GenerationRequest(model="m:1", prompt="p", max_tokens=1))


@respx.mock
async def test_invalid_json_is_provider_error(provider):
    respx.get(f"{BASE}/api/tags").respond(200, text="<html>proxy error</html>")
    with pytest.raises(ProviderError):
        await provider.list_models()


async def test_registry_builds_ollama_provider():
    provider = create_provider(build_settings(ollama_base_url=BASE))
    try:
        assert isinstance(provider, OllamaProvider)
    finally:
        await provider.close()
