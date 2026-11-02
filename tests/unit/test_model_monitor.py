"""Background model-availability probe."""

from __future__ import annotations

import asyncio

from prometheus_client import REGISTRY

from app.providers.base import ModelInfo, ProviderUnavailableError
from app.services.model_monitor import ModelAvailabilityMonitor
from tests.conftest import build_settings


def gauge(model: str) -> float | None:
    return REGISTRY.get_sample_value("llmforge_model_availability", {"provider": "fake", "model": model})


async def test_probe_loop_updates_gauge_for_all_allowed_models(fake_provider):
    fake_provider.models = [ModelInfo(name="llama3.2:1b")]
    settings = build_settings(allowed_models_csv="qwen2.5:0.5b", model_probe_interval_seconds=0.01)
    monitor = ModelAvailabilityMonitor(fake_provider, settings)
    task = asyncio.create_task(monitor.run_forever())
    await asyncio.sleep(0.05)
    assert gauge("llama3.2:1b") == 1
    assert gauge("qwen2.5:0.5b") == 0

    fake_provider.list_error = ProviderUnavailableError()
    await asyncio.sleep(0.05)
    assert gauge("llama3.2:1b") == 0
    task.cancel()


async def test_probe_loop_survives_unexpected_errors(fake_provider):
    fake_provider.list_error = RuntimeError("boom")
    monitor = ModelAvailabilityMonitor(fake_provider, build_settings(model_probe_interval_seconds=0.01))
    task = asyncio.create_task(monitor.run_forever())
    await asyncio.sleep(0.05)
    assert not task.done()
    fake_provider.list_error = None
    await asyncio.sleep(0.05)
    assert gauge("llama3.2:1b") == 1
    task.cancel()


def test_lifespan_starts_and_stops_probe(make_client, fake_provider):
    calls = 0
    original = fake_provider.list_models

    async def counting():
        nonlocal calls
        calls += 1
        return await original()

    fake_provider.list_models = counting
    client = make_client(model_probe_interval_seconds=0.01)
    client.get("/health")
    import time

    time.sleep(0.1)
    client.__exit__(None, None, None)
    assert calls >= 1
