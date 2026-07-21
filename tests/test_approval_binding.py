"""Adversarial tests for the approval-binding path.

CourseFuzz never writes to a destination on a button click: it writes only the exact bytes an
instructor approved by hash, under a one-time token bound to that exact payload. These tests attack
the guards that enforce it — a stale/forged hash, a forged token, a rotated token, and a replayed
apply — and assert the system refuses to act rather than write something unapproved.

The engine runs offline (deterministic provider) against the seeded triangle assignment, whose
analysis deterministically yields one verified candidate, and writes to the local-artifact
destination, so every assertion here is fully reproducible without network or model access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coursefuzz.domain.models import MutationMetrics, RunStatus
from coursefuzz.main import create_app


def _service(tmp_path: Path):
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    return app.state.run_service


def _run_awaiting_approval(service, key: str):
    """Drive one run to APPROVAL_REQUIRED and return (run_id, payload_sha256)."""

    run, _ = service.create_run("triangle-classifier", key)
    service.analyze_run(run.id)
    analyzed = service.require_run(run.id)
    assert analyzed.status == RunStatus.APPROVAL_REQUIRED
    assert analyzed.analysis and analyzed.analysis.candidate
    return run.id, analyzed.analysis.candidate.payload_sha256


def test_approval_rejects_a_hash_that_is_not_the_exact_proposed_payload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id, _payload = _run_awaiting_approval(service, "stale-hash")

    with pytest.raises(ValueError, match="does not match the exact proposed payload"):
        service.approve(run_id, "0" * 64)

    # The run stays awaiting approval; a wrong hash never advances it.
    assert service.require_run(run_id).status == RunStatus.APPROVAL_REQUIRED


def test_apply_rejects_a_forged_token_and_writes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id, payload = _run_awaiting_approval(service, "forged-token")
    service.approve(run_id, payload)  # a real token is issued and deliberately discarded

    with pytest.raises(ValueError, match="Approval token is invalid for this exact payload"):
        service.apply(run_id, "forged-token-value")

    # apply() refuses before touching the destination: still APPROVED, no artifact persisted.
    assert service.require_run(run_id).status == RunStatus.APPROVED
    assert service.repository.artifact(run_id) is None


def test_reapproval_rotates_the_token_and_invalidates_the_old_one(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id, payload = _run_awaiting_approval(service, "rotate-token")

    first = service.approve(run_id, payload)
    second = service.approve(run_id, payload)
    assert first.approval_token != second.approval_token

    # The superseded token can no longer apply...
    with pytest.raises(ValueError, match="Approval token is invalid for this exact payload"):
        service.apply(run_id, first.approval_token)
    assert service.require_run(run_id).status == RunStatus.APPROVED

    # ...but the current token completes the verified write.
    verified = service.apply(run_id, second.approval_token)
    assert verified.status == RunStatus.VERIFIED
    assert verified.action_receipt is not None
    assert verified.action_receipt.read_back_verified is True


def test_apply_after_verification_is_idempotent_and_does_not_rewrite(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id, payload = _run_awaiting_approval(service, "replay-apply")
    receipt = service.approve(run_id, payload)

    verified = service.apply(run_id, receipt.approval_token)
    assert verified.status == RunStatus.VERIFIED
    first_artifact = service.repository.artifact(run_id)
    assert first_artifact is not None

    # Replaying apply on a verified run short-circuits: same result, no second write.
    replay = service.apply(run_id, receipt.approval_token)
    assert replay.status == RunStatus.VERIFIED
    assert replay.artifact_sha256 == verified.artifact_sha256
    assert service.repository.artifact(run_id).sha256 == first_artifact.sha256


def test_consume_approval_is_bound_to_the_exact_token_and_payload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id, payload = _run_awaiting_approval(service, "repo-binding")
    token, _approved_at = service.repository.approve(run_id, payload)

    # A wrong payload or a wrong token is refused; only the exact pair consumes the approval.
    assert service.repository.consume_approval(run_id, token, "deadbeef") is False
    assert service.repository.consume_approval(run_id, "wrong-token", payload) is False
    assert service.repository.consume_approval(run_id, token, payload) is True
    assert service.repository.consume_approval(run_id, token, payload) is False


def test_apply_fails_closed_when_post_write_metrics_diverge_from_projection(tmp_path: Path) -> None:
    """After writing, CourseFuzz reruns the corpus and refuses to report success unless the result
    matches the approved projection. Corrupt the recorded projection so the honest post-write
    measurement cannot match it, and assert the run reverts to a retryable APPROVED state with no
    verified receipt or persisted artifact — never a false 'verified'.
    """

    service = _service(tmp_path)
    run_id, payload = _run_awaiting_approval(service, "post-write-divergence")

    run = service.require_run(run_id)
    assert run.analysis and run.analysis.candidate
    total = run.analysis.projected_after.total_mutants
    impossible_projection = MutationMetrics(
        total_mutants=total,
        killed_mutants=0,
        surviving_mutants=total,
        mutation_score=0.0,
        accepted_solution_pass_rate=100.0,
    )
    tampered = run.analysis.model_copy(update={"projected_after": impossible_projection})
    service.repository.save(run.model_copy(update={"analysis": tampered}))

    receipt = service.approve(run_id, payload)
    with pytest.raises(RuntimeError, match="diverged from the approved projection"):
        service.apply(run_id, receipt.approval_token)

    result = service.require_run(run_id)
    assert result.status == RunStatus.APPROVED  # retryable, not VERIFIED
    assert "diverged" in (result.error or "")
    assert result.action_receipt is None
    assert service.repository.artifact(run_id) is None
