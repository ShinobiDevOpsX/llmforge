"""Authentication, configuration validation, rate limiting and concurrency limits."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.services.rate_limiter import TokenBucketRateLimiter
from tests.conftest import TEST_API_KEY, build_settings

PROMPT = {"prompt": "hi"}


def test_missing_api_key_rejected(client):
    response = client.post("/v1/generate", json=PROMPT)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["www-authenticate"] == "ApiKey"


def test_invalid_api_key_rejected(client):
    response = client.post("/v1/generate", json=PROMPT, headers={"X-API-Key": "wrong-key-wrong-key"})
    assert response.status_code == 401


def test_models_endpoint_requires_auth(client):
    assert client.get("/v1/models").status_code == 401


def test_bearer_token_accepted(client):
    response = client.post("/v1/generate", json=PROMPT, headers={"Authorization": f"Bearer {TEST_API_KEY}"})
    assert response.status_code == 200


def test_any_configured_key_accepted(make_client):
    second = "second-key-abcdefghijkl"
    client = make_client(api_keys=f"{TEST_API_KEY}, {second}")
    assert client.post("/v1/generate", json=PROMPT, headers={"X-API-Key": second}).status_code == 200


def test_auth_can_be_disabled(make_client):
    client = make_client(auth_enabled=False, api_keys="")
    assert client.post("/v1/generate", json=PROMPT).status_code == 200


def test_auth_failure_checked_before_body_validation(client):
    assert client.post("/v1/generate", json={"prompt": ""}).status_code == 401


def test_auth_enabled_without_keys_fails_fast():
    with pytest.raises(ValidationError, match="LLMFORGE_API_KEYS is empty"):
        Settings(_env_file=None, auth_enabled=True, api_keys="")


def test_short_api_keys_refused():
    with pytest.raises(ValidationError, match="at least 16"):
        Settings(_env_file=None, api_keys="short")


def test_default_tokens_cannot_exceed_max():
    with pytest.raises(ValidationError):
        build_settings(default_max_tokens=500, max_output_tokens=100)


def test_api_keys_not_exposed_in_repr():
    assert TEST_API_KEY not in repr(build_settings())


def test_unknown_provider_rejected():
    from app.providers.registry import create_provider

    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider(build_settings(provider="nope"))


# --- Rate limiting --------------------------------------------------------


def test_rate_limit_returns_429_with_retry_after(make_client, auth_headers):
    client = make_client(rate_limit_requests_per_minute=60, rate_limit_burst=2)
    codes = [client.post("/v1/generate", json=PROMPT, headers=auth_headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    response = client.post("/v1/generate", json=PROMPT, headers=auth_headers)
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1


def test_rate_limit_does_not_apply_to_health(make_client):
    client = make_client(rate_limit_requests_per_minute=1, rate_limit_burst=1)
    assert all(client.get("/health").status_code == 200 for _ in range(5))


def test_rate_limit_can_be_disabled(make_client, auth_headers):
    client = make_client(rate_limit_enabled=False)
    for _ in range(20):
        assert client.post("/v1/generate", json=PROMPT, headers=auth_headers).status_code == 200


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_token_bucket_refills_over_time():
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1, clock=clock)
    assert limiter.acquire("a") == (True, 0)
    allowed, retry_after = limiter.acquire("a")
    assert allowed is False
    assert retry_after == 1
    clock.now += 1.0
    assert limiter.acquire("a")[0] is True


def test_token_bucket_isolates_clients():
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=1, clock=FakeClock())
    assert limiter.acquire("a")[0] is True
    assert limiter.acquire("b")[0] is True
    assert limiter.acquire("a")[0] is False


def test_token_bucket_memory_is_bounded():
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(requests_per_minute=60, burst=5, max_clients=3, clock=clock)
    for i in range(10):
        clock.now += 0.01
        limiter.acquire(f"client-{i}")
    assert len(limiter._buckets) <= 3


# --- Concurrency protection ----------------------------------------------


async def test_excess_concurrent_generations_rejected(fake_provider):
    fake_provider.delay = 0.3
    app = create_app(
        build_settings(max_concurrent_generations=1, queue_timeout_seconds=0.05), provider=fake_provider
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": TEST_API_KEY}
        responses = await asyncio.gather(
            ac.post("/v1/generate", json=PROMPT, headers=headers),
            ac.post("/v1/generate", json=PROMPT, headers=headers),
        )
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 503]
    rejected = next(r for r in responses if r.status_code == 503)
    assert rejected.json()["error"]["code"] == "overloaded"
    assert "retry-after" in rejected.headers


async def test_queued_request_proceeds_when_slot_frees(fake_provider):
    fake_provider.delay = 0.05
    app = create_app(
        build_settings(max_concurrent_generations=1, queue_timeout_seconds=2), provider=fake_provider
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": TEST_API_KEY}
        responses = await asyncio.gather(
            *(ac.post("/v1/generate", json=PROMPT, headers=headers) for _ in range(3))
        )
    assert [r.status_code for r in responses] == [200, 200, 200]


async def test_zero_queue_timeout_rejects_immediately(fake_provider):
    fake_provider.delay = 0.2
    app = create_app(
        build_settings(max_concurrent_generations=1, queue_timeout_seconds=0), provider=fake_provider
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": TEST_API_KEY}
        responses = await asyncio.gather(
            ac.post("/v1/generate", json=PROMPT, headers=headers),
            ac.post("/v1/generate", json=PROMPT, headers=headers),
        )
    assert sorted(r.status_code for r in responses) == [200, 503]
