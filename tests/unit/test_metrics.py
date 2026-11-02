"""Prometheus metrics exposure and correctness."""

from __future__ import annotations

from prometheus_client import REGISTRY

from app.providers.base import ProviderUnavailableError


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_endpoint_exposes_required_metrics(client, auth_headers):
    client.post("/v1/generate", json={"prompt": "hi"}, headers=auth_headers)
    client.get("/ready")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for name in (
        "llmforge_requests_total",
        "llmforge_request_duration_seconds",
        "llmforge_input_tokens_total",
        "llmforge_output_tokens_total",
        "llmforge_generation_errors_total",
        "llmforge_model_availability",
        "llmforge_active_requests",
        "llmforge_inference_duration_seconds",
        "llmforge_build_info",
    ):
        assert name in response.text, name


def test_metrics_do_not_require_auth(client):
    assert client.get("/metrics").status_code == 200


def test_generation_increments_counters(client, auth_headers):
    labels = {"provider": "fake", "model": "llama3.2:1b"}
    before_in = sample("llmforge_input_tokens_total", **labels)
    before_out = sample("llmforge_output_tokens_total", **labels)
    before_req = sample("llmforge_requests_total", method="POST", route="/v1/generate", status="200")
    before_inf = sample("llmforge_inference_duration_seconds_count", outcome="success", **labels)

    assert client.post("/v1/generate", json={"prompt": "hi"}, headers=auth_headers).status_code == 200

    assert sample("llmforge_input_tokens_total", **labels) == before_in + 10
    assert sample("llmforge_output_tokens_total", **labels) == before_out + 5
    assert (
        sample("llmforge_requests_total", method="POST", route="/v1/generate", status="200") == before_req + 1
    )
    assert sample("llmforge_inference_duration_seconds_count", outcome="success", **labels) == before_inf + 1


def test_missing_token_counts_do_not_change_token_metrics(client, auth_headers, fake_provider):
    fake_provider.prompt_tokens = None
    fake_provider.completion_tokens = None
    labels = {"provider": "fake", "model": "llama3.2:1b"}
    before = sample("llmforge_input_tokens_total", **labels)
    client.post("/v1/generate", json={"prompt": "hi"}, headers=auth_headers)
    assert sample("llmforge_input_tokens_total", **labels) == before


def test_errors_counted_by_type(client, auth_headers, fake_provider):
    fake_provider.error = ProviderUnavailableError()
    labels = {"provider": "fake", "model": "llama3.2:1b", "error_type": "runtime_unavailable"}
    before = sample("llmforge_generation_errors_total", **labels)
    client.post("/v1/generate", json={"prompt": "hi"}, headers=auth_headers)
    assert sample("llmforge_generation_errors_total", **labels) == before + 1


def test_model_availability_gauge_follows_readiness(client, fake_provider):
    client.get("/ready")
    assert sample("llmforge_model_availability", provider="fake", model="llama3.2:1b") == 1
    fake_provider.list_error = ProviderUnavailableError()
    client.get("/ready")
    assert sample("llmforge_model_availability", provider="fake", model="llama3.2:1b") == 0


def test_unmatched_paths_share_one_label(client):
    before = sample("llmforge_requests_total", method="GET", route="unmatched", status="404")
    client.get("/random/abc")
    client.get("/random/xyz")
    assert sample("llmforge_requests_total", method="GET", route="unmatched", status="404") == before + 2


def test_active_requests_returns_to_baseline(client):
    client.get("/health")
    assert sample("llmforge_active_requests") == 0


def test_error_and_token_series_exported_as_zero_before_first_use(make_client):
    make_client(allowed_models_csv="zero-init-model:1b")
    labels = {"provider": "fake", "model": "zero-init-model:1b"}
    assert REGISTRY.get_sample_value("llmforge_input_tokens_total", labels) == 0
    assert REGISTRY.get_sample_value("llmforge_output_tokens_total", labels) == 0
    for error_type in ("runtime_unavailable", "timeout", "model_not_found", "provider_error", "overloaded"):
        value = REGISTRY.get_sample_value(
            "llmforge_generation_errors_total", {**labels, "error_type": error_type}
        )
        assert value == 0, error_type


def test_v1_request_series_preinitialized(client):
    for status in ("200", "401", "429", "503", "504"):
        labels = {"method": "POST", "route": "/v1/generate", "status": status}
        assert REGISTRY.get_sample_value("llmforge_requests_total", labels) is not None, status
    labels = {"method": "GET", "route": "/v1/models", "status": "503"}
    assert REGISTRY.get_sample_value("llmforge_requests_total", labels) is not None
