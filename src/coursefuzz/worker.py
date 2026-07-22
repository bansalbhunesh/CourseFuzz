"""A separate execution worker that analyzes queued runs on a selectable backend.

The API enqueues runs; a worker process claims and analyzes them out of band. This is the process
that lets analysis run on the isolated container backend (``GVisorDockerRunner``) on a runsc-capable
host instead of inside the API. It reuses the already-tested ``recover_incomplete_runs`` claim loop,
so a worker is a thin, durable poller over shared repository state.

The backend is chosen by ``COURSEFUZZ_EXECUTION_BACKEND`` (``local`` | ``docker`` | ``gvisor``).
For a true API/worker split the API is deployed with ``COURSEFUZZ_DEFER_ANALYSIS=1`` so runs stay
queued for the worker; without it, ``analyze_run``'s ``status == QUEUED`` guard still prevents
double analysis, it is just redundant.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from coursefuzz.adapters.hypotheses import build_hypothesis_provider
from coursefuzz.adapters.isolated_runner import DockerIsolatedRunner, GVisorDockerRunner
from coursefuzz.adapters.sandbox import LocalRestrictedRunner
from coursefuzz.config import analysis_deadline_seconds
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT, TRIANGLE_GITHUB_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.execution import ExecutionGateway
from coursefuzz.repositories.postgres import PostgresRunRepository
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService


def build_execution_backend(name: str | None = None) -> ExecutionGateway:
    """Resolve the configured execution backend. Every option satisfies ExecutionGateway."""

    selected = (name or os.getenv("COURSEFUZZ_EXECUTION_BACKEND", "local")).lower()
    if selected == "local":
        return LocalRestrictedRunner()
    if selected == "docker":
        return DockerIsolatedRunner()
    if selected == "gvisor":
        return GVisorDockerRunner()
    raise ValueError(
        f"Unknown COURSEFUZZ_EXECUTION_BACKEND {selected!r}; expected local, docker, or gvisor"
    )


def build_worker_service(
    backend: ExecutionGateway | None = None,
    *,
    database_path: str | Path | None = None,
    artifact_dir: str | Path | None = None,
) -> RunService:
    """Construct a RunService whose engine executes on the chosen isolated/local backend."""

    backend = backend or build_execution_backend()
    provider = build_hypothesis_provider()
    database_url = os.getenv("DATABASE_URL") if database_path is None else None
    repository = (
        PostgresRunRepository(database_url)
        if database_url
        else RunRepository(database_path or os.getenv("COURSEFUZZ_DB_PATH", "coursefuzz.db"))
    )
    assignment_service = AssignmentService(repository, backend)
    assignment_service.seed(TRIANGLE_ASSIGNMENT)
    assignment_service.seed(TRIANGLE_GITHUB_ASSIGNMENT)
    engine = AssessmentEngine(
        backend,
        provider,
        max_analysis_seconds=analysis_deadline_seconds(),
    )
    return RunService(
        repository,
        engine,
        assignment_service,
        artifact_dir or os.getenv("COURSEFUZZ_ARTIFACT_DIR", "data/artifacts"),
        provider.mode,
    )


def run_worker(
    service: RunService,
    *,
    interval: float = 2.0,
    max_iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Poll for queued/interrupted runs and analyze them, returning the number processed.

    ``max_iterations`` bounds the loop for tests and one-shot drains; production leaves it None.
    """

    processed = 0
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        processed += service.recover_incomplete_runs()
        iteration += 1
        if max_iterations is not None and iteration >= max_iterations:
            break
        sleep(interval)
    return processed


def main() -> None:  # pragma: no cover - process entrypoint
    backend_name = os.getenv("COURSEFUZZ_EXECUTION_BACKEND", "local")
    service = build_worker_service()
    print(f"CourseFuzz worker started (backend={backend_name})", flush=True)
    run_worker(service)


if __name__ == "__main__":  # pragma: no cover
    main()
