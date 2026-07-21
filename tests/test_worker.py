"""Tests for the separate execution worker and its backend selector."""

from __future__ import annotations

from pathlib import Path

import pytest

from coursefuzz.adapters.isolated_runner import DockerIsolatedRunner, GVisorDockerRunner
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.domain.models import RunStatus
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
