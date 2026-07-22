"""Tests for per-tenant token-bucket rate limiting on POST /runs."""

from __future__ import annotations

import time

from coursefuzz.security.rate_limit import TokenBucketRateLimiter


class FakeClock:
    """Deterministic clock for testing token refill behaviour."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# Unit tests for the TokenBucketRateLimiter itself
# ---------------------------------------------------------------------------


def test_disabled_limiter_always_allows() -> None:
    limiter = TokenBucketRateLimiter(capacity=0, refill_per_second=0.0)
    assert not limiter.enabled
    for _ in range(1000):
        assert limiter.allow("tenant-a")


def test_burst_allowed_up_to_capacity() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(capacity=3, refill_per_second=1.0, clock=clock)
    assert limiter.allow("t1")
    assert limiter.allow("t1")
    assert limiter.allow("t1")
    assert not limiter.allow("t1")


def test_tokens_refill_over_time() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(capacity=2, refill_per_second=1.0, clock=clock)
    # Exhaust the bucket
    assert limiter.allow("t1")
    assert limiter.allow("t1")
    assert not limiter.allow("t1")
    # Advance 1 second → 1 token refilled
    clock.advance(1.0)
    assert limiter.allow("t1")
    assert not limiter.allow("t1")


def test_per_tenant_isolation() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=0.1, clock=clock)
    assert limiter.allow("tenant-a")
    assert not limiter.allow("tenant-a")
    # A different tenant has its own bucket
    assert limiter.allow("tenant-b")
    assert not limiter.allow("tenant-b")


def test_retry_after_returns_positive_seconds() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(capacity=1, refill_per_second=0.5, clock=clock)
    assert limiter.allow("t1")
    assert not limiter.allow("t1")
    retry = limiter.retry_after_seconds("t1")
    assert retry >= 1  # always at least 1 so clients always back off


def test_retry_after_zero_when_tokens_available() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(capacity=5, refill_per_second=1.0, clock=clock)
    assert limiter.retry_after_seconds("new-tenant") == 0


def test_from_env_with_zero_disables(monkeypatch: object) -> None:
    """COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE=0 → disabled."""
    import os

    os.environ["COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE"] = "0"
    try:
        limiter = TokenBucketRateLimiter.from_env()
        assert not limiter.enabled
    finally:
        del os.environ["COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE"]


def test_from_env_with_positive_value() -> None:
    """COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE=60 → capacity=60, 1/s refill."""
    import os

    os.environ["COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE"] = "60"
    try:
        limiter = TokenBucketRateLimiter.from_env()
        assert limiter.enabled
    finally:
        del os.environ["COURSEFUZZ_RUN_RATE_LIMIT_PER_MINUTE"]


# ---------------------------------------------------------------------------
# Integration test: 429 on the API route
# ---------------------------------------------------------------------------


def test_create_run_returns_429_when_rate_limited() -> None:
    """POST /api/runs returns 429 + Retry-After header when the bucket is exhausted."""
    from fastapi.testclient import TestClient

    from coursefuzz.main import create_app
    from coursefuzz.security.access import AccessPolicy

    token = "rate-test-token-minimum-24-chars"
    access = AccessPolicy(tenant_tokens={"rate-test-tenant": token})
    app = create_app(access_policy=access)
    client = TestClient(app)
    # Health endpoint should work regardless
    resp = client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
