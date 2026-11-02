"""Integration test configuration.

Run against a live stack (``docker compose up`` + model pulled):

    pytest -m integration

Settings:
    LLMFORGE_IT_API_URL   API base URL (default http://127.0.0.1:8000)
    LLMFORGE_IT_API_KEY   API key (default: first key in ./.env)
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _key_from_dotenv() -> str | None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        if line.startswith("LLMFORGE_API_KEYS="):
            return line.split("=", 1)[1].split(",")[0].strip() or None
    return None


@pytest.fixture(scope="session")
def api_url() -> str:
    return os.environ.get("LLMFORGE_IT_API_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.environ.get("LLMFORGE_IT_API_KEY") or _key_from_dotenv()
    if not key:
        pytest.fail("Set LLMFORGE_IT_API_KEY or create .env (scripts/setup.sh)")
    return key


@pytest.fixture(scope="session")
def live_client(api_url: str, api_key: str):
    try:
        httpx.get(f"{api_url}/health", timeout=3).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"LLMForge API not reachable at {api_url}: {exc}")
    with httpx.Client(base_url=api_url, headers={"X-API-Key": api_key}, timeout=300) as client:
        yield client
