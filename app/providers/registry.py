"""Maps the ``LLMFORGE_PROVIDER`` setting to a provider implementation."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.providers.base import LLMProvider
from app.providers.ollama import OllamaProvider

ProviderFactory = Callable[[Settings], LLMProvider]


def _ollama(settings: Settings) -> LLMProvider:
    return OllamaProvider(
        settings.ollama_base_url,
        connect_timeout=settings.connect_timeout_seconds,
        generation_timeout=settings.generation_timeout_seconds,
        probe_timeout=settings.probe_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
    )


_REGISTRY: dict[str, ProviderFactory] = {"ollama": _ollama}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _REGISTRY[name] = factory


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def create_provider(settings: Settings) -> LLMProvider:
    try:
        factory = _REGISTRY[settings.provider]
    except KeyError:
        raise ValueError(
            f"Unknown provider '{settings.provider}'. Available: {', '.join(available_providers())}"
        ) from None
    return factory(settings)
