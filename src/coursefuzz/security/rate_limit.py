from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    """Thread-safe per-key token bucket for bounding run creation on a public self-serve service.

    Capacity is the burst size and ``refill_per_second`` the steady rate. The limiter is in-process
    (per instance); a multi-instance deployment needs a shared counter (Milestone 6). A capacity of
    zero disables limiting entirely, which is the default so existing single-tenant demos are
    unaffected.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = max(0, capacity)
        self._refill = max(0.0, refill_per_second)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._capacity > 0

    def _replenish(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(float(self._capacity), bucket.tokens + elapsed * self._refill)
        bucket.updated_at = now

    def allow(self, key: str) -> bool:
        """Consume one token for ``key``; return ``True`` if permitted, ``False`` if rate-limited."""

        if not self.enabled:
            return True
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=self._capacity - 1, updated_at=now)
                return True
            self._replenish(bucket, now)
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return True
            return False

    def retry_after_seconds(self, key: str) -> int:
        """Whole seconds until ``key`` regains at least one token (>=1 so a client always backs off)."""

        if not self.enabled or self._refill <= 0:
            return 1
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            self._replenish(bucket, now)
            if bucket.tokens >= 1:
                return 0
            return max(1, int((1 - bucket.tokens) / self._refill + 0.999))

    @classmethod
    def from_env(cls) -> TokenBucketRateLimiter:
        raw = os.getenv("COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE", "0").strip()
        try:
            per_minute = int(raw)
        except ValueError:
            per_minute = 0
        per_minute = max(0, per_minute)
        return cls(capacity=per_minute, refill_per_second=per_minute / 60.0 if per_minute else 0.0)
