"""POST /v1/generate and GET /v1/models."""

from __future__ import annotations

import pytest

from app.providers.base import (
    ModelInfo,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def generate(client, headers, **body):
    body.setdefault("prompt", "Explain Kubernetes")
    return client.post("/v1/generate", json=body, headers=headers)


def test_generate_success(client, auth_headers, fake_provider):
    response = generate(client, auth_headers, model="llama3.2:1b")
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "llama3.2:1b"
    assert body["response"] == "Kubernetes orchestrates containers."
    assert body["prompt_tokens"] == 10
    assert body["completion_tokens"] == 5
    assert body["duration_seconds"] >= 0
    assert response.headers["cache-control"] == "no-store"
    assert fake_provider.requests[0].max_tokens == 256


def test_generate_uses_default_model(client, auth_headers, fake_provider):
    assert generate(client, auth_headers).status_code == 200
    assert fake_provider.requests[0].model == "llama3.2:1b"


def test_generate_passes_options(client, auth_headers, fake_provider):
    assert generate(client, auth_headers, max_tokens=32, temperature=0.2).status_code == 200
    assert fake_provider.requests[0].max_tokens == 32
    assert fake_provider.requests[0].temperature == 0.2


def test_missing_token_counts_are_null(client, auth_headers, fake_provider):
    fake_provider.prompt_tokens = None
    fake_provider.completion_tokens = None
    body = generate(client, auth_headers).json()
    assert body["prompt_tokens"] is None
    assert body["completion_tokens"] is None


@pytest.mark.parametrize("prompt", ["", "   \n\t "])
def test_empty_prompt_rejected(client, auth_headers, fake_provider, prompt):
    response = generate(client, auth_headers, prompt=prompt)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert fake_provider.requests == []


def test_excessive_prompt_rejected(make_client, auth_headers, fake_provider):
    client = make_client(max_prompt_chars=100)
    response = generate(client, auth_headers, prompt="x" * 101)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "prompt_too_long"
    assert fake_provider.requests == []


def test_prompt_is_not_echoed_in_validation_errors(client, auth_headers):
    secret_prompt = "my-confidential-prompt"
    response = client.post(
        "/v1/generate", json={"prompt": secret_prompt, "temperature": 9}, headers=auth_headers
    )
    assert response.status_code == 422
    assert secret_prompt not in response.text


def test_disallowed_model_rejected(client, auth_headers, fake_provider):
    response = generate(client, auth_headers, model="llama3.1:405b")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "model_not_allowed"
    assert fake_provider.requests == []


def test_additional_allowed_model_accepted(make_client, auth_headers):
    client = make_client(allowed_models_csv="qwen2.5:0.5b")
    assert generate(client, auth_headers, model="qwen2.5:0.5b").status_code == 200


@pytest.mark.parametrize("model", ["../etc/passwd x", "a" * 200, "model;rm"])
def test_malformed_model_name_rejected(client, auth_headers, model):
    assert generate(client, auth_headers, model=model).status_code == 422


def test_unknown_fields_rejected(client, auth_headers):
    assert generate(client, auth_headers, stream=True).status_code == 422


def test_max_tokens_above_limit_rejected(make_client, auth_headers):
    client = make_client(max_output_tokens=300)
    response = generate(client, auth_headers, max_tokens=301)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_parameter"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ProviderUnavailableError(), 503, "runtime_unavailable"),
        (ModelNotFoundError("llama3.2:1b"), 503, "model_not_available"),
        (ProviderTimeoutError(), 504, "runtime_timeout"),
        (ProviderError(), 502, "runtime_error"),
    ],
)
def test_provider_errors_mapped(client, auth_headers, fake_provider, error, status, code):
    fake_provider.error = error
    response = generate(client, auth_headers)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_generation_deadline_enforced(make_client, auth_headers, fake_provider):
    fake_provider.delay = 1.0
    client = make_client(generation_timeout_seconds=0.05)
    response = generate(client, auth_headers)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "runtime_timeout"


def test_unexpected_error_does_not_leak_details(client, auth_headers, fake_provider):
    fake_provider.error = RuntimeError("db password=hunter2 at /srv/app.py line 42")
    response = generate(client, auth_headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text


def test_oversized_body_rejected(make_client, auth_headers, fake_provider):
    client = make_client(max_request_body_bytes=1024, max_prompt_chars=100_000)
    response = generate(client, auth_headers, prompt="x" * 5000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert fake_provider.requests == []


def test_list_models(client, auth_headers, fake_provider):
    fake_provider.models.append(ModelInfo(name="mistral:7b"))
    response = client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["default_model"] == "llama3.2:1b"
    by_name = {m["name"]: m for m in body["models"]}
    assert by_name["llama3.2:1b"]["allowed"] is True
    assert by_name["mistral:7b"]["allowed"] is False


def test_list_models_runtime_down(client, auth_headers, fake_provider):
    fake_provider.list_error = ProviderUnavailableError()
    response = client.get("/v1/models", headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "runtime_unavailable"
