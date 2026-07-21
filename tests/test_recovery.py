from pathlib import Path

from coursefuzz.domain.models import RunStatus, utc_now
from coursefuzz.main import create_app


def test_recovery_replays_interrupted_analysis_from_durable_queue(tmp_path: Path) -> None:
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    service = app.state.run_service
    run, _ = service.create_run("triangle-classifier", "recover-analysis")
    interrupted = run.model_copy(
        update={"status": RunStatus.ANALYZING, "updated_at": utc_now()}
    )
    service.repository.save(interrupted)

    recovered = service.recover_incomplete_runs()
    result = service.require_run(run.id)

    assert recovered == 1
    assert result.status == RunStatus.APPROVAL_REQUIRED
    assert any(
        event.event_type == "run.recovered"
        for event in service.repository.events_after(run.id)
    )


def test_recovery_restores_interrupted_apply_to_reauthorization_boundary(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    service = app.state.run_service
    run, _ = service.create_run("triangle-classifier", "recover-apply")
    service.analyze_run(run.id)
    analyzed = service.require_run(run.id)
    assert analyzed.analysis and analyzed.analysis.candidate
    service.approve(run.id, analyzed.analysis.candidate.payload_sha256)
    approved = service.require_run(run.id)
    service.repository.save(
        approved.model_copy(update={"status": RunStatus.APPLYING, "updated_at": utc_now()})
    )

    recovered = service.recover_incomplete_runs()
    result = service.require_run(run.id)

    assert recovered == 1
    assert result.status == RunStatus.APPROVED
    assert "reauthorize" in (result.error or "")


def test_interrupted_apply_retries_to_exactly_once_verification(tmp_path: Path) -> None:
    """An apply that crashed after consuming the approval but before verifying must be safely
    retryable to a single verified write -- not blocked, and not applied twice.
    """

    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    service = app.state.run_service
    run, _ = service.create_run("triangle-classifier", "retry-idempotency")
    service.analyze_run(run.id)
    analyzed = service.require_run(run.id)
    assert analyzed.analysis and analyzed.analysis.candidate
    payload = analyzed.analysis.candidate.payload_sha256
    token, _approved_at = service.repository.approve(run.id, payload)
    service.repository.save(
        analyzed.model_copy(
            update={"status": RunStatus.APPROVED, "approval_payload_sha256": payload}
        )
    )

    # Simulate a crash mid-apply: the approval was consumed and the run left in APPLYING.
    assert service.repository.consume_approval(run.id, token, payload) is True
    service.repository.save(
        service.require_run(run.id).model_copy(
            update={"status": RunStatus.APPLYING, "updated_at": utc_now()}
        )
    )

    # Recovery returns it to the retryable approved boundary, then the retry completes once.
    service.recover_incomplete_runs()
    assert service.require_run(run.id).status == RunStatus.APPROVED

    verified = service.apply(run.id, token)
    assert verified.status == RunStatus.VERIFIED
    assert verified.action_receipt is not None
    assert verified.action_receipt.read_back_verified is True
    artifact = service.repository.artifact(run.id)
    assert artifact is not None

    # A duplicate delivery of the same apply is a no-op: identical artifact, no second write.
    replay = service.apply(run.id, token)
    assert replay.status == RunStatus.VERIFIED
    assert replay.artifact_sha256 == verified.artifact_sha256
    assert service.repository.artifact(run.id).sha256 == artifact.sha256
