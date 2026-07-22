"""Exactly-once logical apply under concurrency and crash-retry (roadmap Milestone 6).

The approval token authorizes one exact action. The repository consumes that token and transitions
APPROVED -> APPLYING in one transaction, so concurrent deliveries can start at most one destination
action. A failed or interrupted action requires a fresh exact-payload authorization; destination
adapters remain idempotent so an uncertain external write can be safely reconciled.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from coursefuzz.domain.models import RunStatus
from coursefuzz.main import create_app


def _approved_run(tmp_path: Path, key: str):
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    service = app.state.run_service
    run, _ = service.create_run("triangle-classifier", key)
    service.analyze_run(run.id)
    approved = service.require_run(run.id)
    token = service.approve(run.id, approved.analysis.candidate.payload_sha256).approval_token
    return service, run.id, token


class _CountingDestinations:
    """Delegates to the real coordinator but counts destination writes."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.apply_calls = 0

    def apply(
        self, run_id: str, candidate: object, tenant_id: str = "local-demo"
    ):  # type: ignore[no-untyped-def]
        with self._lock:
            self.apply_calls += 1
        return self._inner.apply(run_id, candidate, tenant_id)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


class _FailOnceDestinations:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls = 0

    def apply(
        self, run_id: str, candidate: object, tenant_id: str = "local-demo"
    ):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient destination failure")
        return self._inner.apply(run_id, candidate, tenant_id)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def test_approval_and_status_claim_are_atomically_one_time(tmp_path: Path) -> None:
    service, run_id, token = _approved_run(tmp_path, "claim-primitive")
    run = service.require_run(run_id)
    assert run.analysis and run.analysis.candidate
    applying = run.model_copy(update={"status": RunStatus.APPLYING})
    payload = run.analysis.candidate.payload_sha256

    # The first exact token + APPROVED claim wins. The same consumed token can never claim again.
    assert service.repository.claim_approved_apply(applying, token, payload) is True
    assert service.repository.claim_approved_apply(applying, token, payload) is False
    assert service.require_run(run_id).status == RunStatus.APPLYING


def test_concurrent_apply_writes_to_the_destination_exactly_once(tmp_path: Path) -> None:
    service, run_id, token = _approved_run(tmp_path, "race")
    service.destinations = _CountingDestinations(service.destinations)

    outcomes: list[tuple[str, str]] = []

    def worker() -> None:
        try:
            result = service.apply(run_id, token)
            outcomes.append(("ok", result.status))
        except ValueError as exc:
            outcomes.append(("rejected", type(exc).__name__))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # The core guarantee: six racing applies, exactly one destination write.
    assert service.destinations.apply_calls == 1
    assert service.require_run(run_id).status == RunStatus.VERIFIED
    # No caller crashed; losers were cleanly rejected or short-circuited on the verified run.
    assert all(kind in {"ok", "rejected"} for kind, _ in outcomes)
    assert len(outcomes) == 6
    assert any(kind == "ok" for kind, _ in outcomes)


def test_failed_apply_requires_fresh_exact_payload_authorization(tmp_path: Path) -> None:
    service, run_id, token = _approved_run(tmp_path, "retry")
    service.destinations = _FailOnceDestinations(service.destinations)

    # The first attempt fails after consuming its one-time action authorization.
    with pytest.raises(RuntimeError):
        service.apply(run_id, token)
    assert service.require_run(run_id).status == RunStatus.APPROVED
    assert "reauthorize" in (service.require_run(run_id).error or "")

    # Replaying the consumed token fails closed. A fresh approval for the unchanged exact payload
    # authorizes one new attempt, which the idempotent destination safely completes.
    with pytest.raises(ValueError, match="Approval token is invalid for this exact payload"):
        service.apply(run_id, token)
    run = service.require_run(run_id)
    assert run.analysis and run.analysis.candidate
    replacement = service.approve(run_id, run.analysis.candidate.payload_sha256)
    verified = service.apply(run_id, replacement.approval_token)
    assert verified.status == RunStatus.VERIFIED
    assert service.destinations.calls == 2
