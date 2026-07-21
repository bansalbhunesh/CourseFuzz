from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.models import AssignmentSnapshot, RunStatus, RunView
from coursefuzz.repositories.postgres import PostgresRunRepository

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="POSTGRES_TEST_URL is required for the hosted repository contract",
)


def test_postgres_repository_preserves_tenant_workflow_and_artifact(
    tmp_path: Path,
) -> None:
    assert POSTGRES_TEST_URL is not None
    repository = PostgresRunRepository(POSTGRES_TEST_URL)
    suffix = uuid4().hex
    now = datetime.now(UTC)
    spec = TRIANGLE_ASSIGNMENT.model_copy(update={"id": f"triangle-{suffix}"})
    snapshot = AssignmentSnapshot(
        id=spec.id,
        snapshot_sha256=hashlib.sha256(suffix.encode()).hexdigest(),
        provenance="manual",
        created_at=now,
        spec=spec,
    )

    stored, created = repository.create_assignment(snapshot, "tenant-a")
    assert created is True
    assert repository.get_assignment(stored.id, "tenant-a") == snapshot
    assert repository.get_assignment(stored.id, "tenant-b") is None

    duplicate, duplicate_created = repository.create_assignment(snapshot, "tenant-b")
    assert duplicate == snapshot
    assert duplicate_created is False
    assert repository.get_assignment(stored.id, "tenant-b") == snapshot

    run = RunView(
        id=f"run_{suffix}",
        assignment_id=stored.id,
        assignment_snapshot_sha256=stored.snapshot_sha256,
        status=RunStatus.QUEUED,
        mode="deterministic-fallback",
        created_at=now,
        updated_at=now,
    )
    created_run, run_created = repository.create(run, "request-1", "tenant-a")
    assert run_created is True
    assert created_run == run
    assert repository.get(run.id, "tenant-b") is None

    event = repository.append_event(
        run.id,
        "run.created",
        "ingest",
        "Postgres contract event",
        {"snapshot": stored.snapshot_sha256},
    )
    assert repository.events_after(run.id)[0] == event

    approval_token, _ = repository.approve(run.id, "a" * 64)
    assert repository.consume_approval(run.id, "wrong", "a" * 64) is False
    assert repository.consume_approval(run.id, approval_token, "a" * 64) is True
    assert repository.consume_approval(run.id, approval_token, "a" * 64) is True

    artifact_path = tmp_path / "test_verified.py"
    artifact_bytes = b"def test_verified():\n    assert True\n"
    artifact_path.write_bytes(artifact_bytes)
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    repository.save_artifact(run.id, artifact_path, artifact_sha)
    artifact = repository.artifact(run.id)
    assert artifact is not None
    assert artifact.filename == artifact_path.name
    assert artifact.sha256 == artifact_sha
    assert artifact.content == artifact_bytes
