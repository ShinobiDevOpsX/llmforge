"""Model availability probing.

Used by the readiness endpoint and by a background loop that keeps the
``llmforge_model_availability`` gauge fresh between scrapes, so alerts fire
even when no client is calling /ready.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import Settings
from app.metrics import MODEL_AVAILABILITY
from app.providers.base import LLMProvider, ProviderError, normalize_model_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AvailabilityStatus:
    runtime_reachable: bool
    model_available: bool
    detail: str | None = None


class ModelAvailabilityMonitor:
    def __init__(self, provider: LLMProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self._last: AvailabilityStatus | None = None

    async def check(self) -> AvailabilityStatus:
        """Probe the runtime once and update the gauge for every allowed model."""
        try:
            present = {normalize_model_name(m.name) for m in await self.provider.list_models()}
        except ProviderError as exc:
            for model in self.settings.allowed_models:
                MODEL_AVAILABILITY.labels(self.provider.name, model).set(0)
            status = AvailabilityStatus(False, False, exc.message)
        else:
            for model in self.settings.allowed_models:
                available = normalize_model_name(model) in present
                MODEL_AVAILABILITY.labels(self.provider.name, model).set(1 if available else 0)
            default_ok = normalize_model_name(self.settings.default_model) in present
            status = AvailabilityStatus(
                True,
                default_ok,
                None
                if default_ok
                else f"Model '{self.settings.default_model}' has not been pulled into the runtime.",
            )

        if status != self._last:
            logger.info(
                "model availability changed",
                extra={
                    "model": self.settings.default_model,
                    "runtime_reachable": status.runtime_reachable,
                    "model_available": status.model_available,
                },
            )
            self._last = status
        return status

    async def run_forever(self) -> None:
        interval = self.settings.model_probe_interval_seconds
        while True:
            try:
                await self.check()
            except Exception:  # never let the probe loop die
                logger.exception("model availability probe failed")
            await asyncio.sleep(interval)
