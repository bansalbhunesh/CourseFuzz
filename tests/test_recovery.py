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
