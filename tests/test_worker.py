"""Tests for the separate execution worker and its backend selector."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coursefuzz.adapters.isolated_runner import DockerIsolatedRunner, GVisorDockerRunner
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.models import RunStatus
from coursefuzz.main import create_app
from coursefuzz.worker import build_execution_backend, build_worker_service, run_worker


def test_backend_selector_resolves_each_option() -> None:
    assert isinstance(build_execution_backend("local"), LocalRestrictedRunner)
    assert isinstance(build_execution_backend("docker"), DockerIsolatedRunner)
    gvisor = build_execution_backend("gvisor")
    assert isinstance(gvisor, GVisorDockerRunner)
    assert gvisor.runtime == "runsc"
    with pytest.raises(ValueError, match="Unknown COURSEFUZZ_EXECUTION_BACKEND"):
        build_execution_backend("nonsense")


def test_worker_analyzes_a_queued_run(tmp_path: Path) -> None:
    # Local backend keeps the test fast; the worker loop is backend-agnostic.
    service = build_worker_service(
        LocalRestrictedRunner(),
        database_path=tmp_path / "coursefuzz.db",
        artifact_dir=tmp_path / "artifacts",
    )
    run, created = service.create_run("triangle-classifier", "worker-run")
    assert created
    # A queued run is not analyzed inline at the service layer -- the worker does it.
    assert service.require_run(run.id).status == RunStatus.QUEUED

    processed = run_worker(service, max_iterations=1, sleep=lambda _seconds: None)

    assert processed >= 1
    result = service.require_run(run.id)
    assert result.status == RunStatus.APPROVAL_REQUIRED
    assert result.analysis is not None and result.analysis.candidate is not None


def test_api_defers_analysis_to_the_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With COURSEFUZZ_DEFER_ANALYSIS set, the API enqueues without analyzing, and a worker sharing
    the repository claims and completes the run -- the real API/worker split.
    """

    monkeypatch.setenv("COURSEFUZZ_DEFER_ANALYSIS", "1")
    database_path = tmp_path / "coursefuzz.db"
    artifact_dir = tmp_path / "artifacts"
    client = TestClient(create_app(database_path, artifact_dir))

    created = client.post(
        "/api/runs",
        json={"assignment_id": "triangle-classifier"},
        headers={"Idempotency-Key": "deferred-run"},
    )
    assert created.status_code == 202
    run_id = created.json()["id"]
    # The API left the run queued instead of analyzing it inline.
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "queued"

    worker_service = build_worker_service(
        LocalRestrictedRunner(), database_path=database_path, artifact_dir=artifact_dir
    )
    run_worker(worker_service, max_iterations=1, sleep=lambda _seconds: None)

    # The worker, a separate service over the same repository, completed the analysis.
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "approval_required"
