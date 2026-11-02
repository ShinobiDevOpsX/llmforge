"""Health, readiness, docs and cross-cutting HTTP behaviour."""

from __future__ import annotations

from app.providers.base import ModelInfo, ProviderUnavailableError


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"]


def test_health_does_not_require_auth_or_runtime(client, fake_provider):
    fake_provider.list_error = ProviderUnavailableError()
    assert client.get("/health").status_code == 200


def test_ready_when_model_present(client):
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["runtime_reachable"] is True
    assert body["model_available"] is True


def test_ready_accepts_implicit_latest_tag(make_client, fake_provider):
    fake_provider.models = [ModelInfo(name="phi3:latest")]
    client = make_client(default_model="phi3")
    assert client.get("/ready").status_code == 200


def test_not_ready_when_model_missing(client, fake_provider):
    fake_provider.models = [ModelInfo(name="other:7b")]
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["runtime_reachable"] is True
    assert body["model_available"] is False
    assert "llama3.2:1b" in body["detail"]


def test_not_ready_when_runtime_unreachable(client, fake_provider):
    fake_provider.list_error = ProviderUnavailableError()
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["runtime_reachable"] is False


def test_request_id_generated_and_returned(client):
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) == 32


def test_valid_request_id_is_propagated(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_malicious_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id\nInjected: yes"})
    assert response.headers["x-request-id"] != "bad id\nInjected: yes"
    assert len(response.headers["x-request-id"]) == 32


def test_security_headers_present(client):
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def test_unknown_route_uses_error_envelope(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_docs_enabled_by_default(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "LLMForge"


def test_docs_can_be_disabled(make_client):
    client = make_client(docs_enabled=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_provider_closed_on_shutdown(make_client, fake_provider):
    client = make_client()
    client.__exit__(None, None, None)
    assert fake_provider.closed is True
