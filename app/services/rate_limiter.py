"""In-memory token-bucket rate limiter.

Scope: a single API process. Running several replicas multiplies the effective
limit; a shared store (e.g. Redis) would be needed for a global limit.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class TokenBucketRateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        burst: int,
        *,
        max_clients: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = requests_per_minute / 60.0
        self.capacity = float(burst)
        self.max_clients = max_clients
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, client_id: str) -> tuple[bool, int]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                if len(self._buckets) >= self.max_clients:
                    self._evict(now)
                bucket = _Bucket(tokens=self.capacity, updated=now)
                self._buckets[client_id] = bucket
            else:
                elapsed = now - bucket.updated
                bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.rate)
                bucket.updated = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            return False, max(1, math.ceil((1.0 - bucket.tokens) / self.rate))

    def _evict(self, now: float) -> None:
        """Drop buckets that have fully refilled; they carry no state worth keeping."""
        refill_time = self.capacity / self.rate
        stale = [k for k, b in self._buckets.items() if now - b.updated >= refill_time]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= self.max_clients:
            oldest = min(self._buckets, key=lambda k: self._buckets[k].updated)
            del self._buckets[oldest]
