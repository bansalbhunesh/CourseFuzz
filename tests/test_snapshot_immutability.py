"""Adversarial tests for the assignment-snapshot immutability guard.

Every assignment is canonicalized into a content-addressed SHA-256 snapshot, and every run stores
the exact hash it was bound to. Two guarantees make that meaningful, and both are attacked here:

1. Drift refusal: if the assignment a run resolves to no longer hashes to the run's bound value,
   the run fails closed instead of analyzing or repairing different content than was reviewed.
2. Content sensitivity: the snapshot hash covers the fields that change grading behavior — domain,
   destination, instructor tests, and program source — while remaining independent of the mutable
   ``id`` label the guard is not allowed to trust.
"""

from __future__ import annotations

from pathlib import Path

from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.models import GitHubPullRequestDestination, RunStatus
from coursefuzz.main import create_app
from coursefuzz.services.assignment_service import _snapshot_sha256


def _service(tmp_path: Path):
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    return app.state.run_service


def test_run_fails_closed_when_its_bound_snapshot_hash_drifts(tmp_path: Path) -> None:
    """A run carries the snapshot hash it was bound to. If the resolved assignment no longer
    matches that hash, analysis must refuse rather than silently run against changed content.
    """

    service = _service(tmp_path)
    run, _ = service.create_run("triangle-classifier", "snapshot-drift")
    assert run.assignment_snapshot_sha256  # bound at creation

    # Simulate content drift: the run now remembers a hash the assignment no longer resolves to.
    drifted = run.model_copy(
        update={"assignment_snapshot_sha256": "0" * 64, "updated_at": run.updated_at}
    )
    service.repository.save(drifted)

    service.analyze_run(run.id)
    result = service.require_run(run.id)

    assert result.status == RunStatus.FAILED
    assert "snapshot hash no longer matches" in (result.error or "")
    # It failed at the guard, before producing any analysis of the changed content.
    assert result.analysis is None
    assert any(
        event.event_type == "run.failed" for event in service.repository.events_after(run.id)
    )


def test_snapshot_hash_is_content_addressed_and_id_independent() -> None:
    """The content address must ignore the id label but change with any grading-relevant field."""

    base = _snapshot_sha256(TRIANGLE_ASSIGNMENT)

    # The id is explicitly excluded: the same content under a different id is the same snapshot.
    relabeled = TRIANGLE_ASSIGNMENT.model_copy(update={"id": "a-totally-different-id"})
    assert _snapshot_sha256(relabeled) == base

    # Each grading-relevant change must produce a distinct content address.
    domain_changed = _snapshot_sha256(
        TRIANGLE_ASSIGNMENT.model_copy(update={"domain_max": TRIANGLE_ASSIGNMENT.domain_max + 1})
    )
    destination_changed = _snapshot_sha256(
        TRIANGLE_ASSIGNMENT.model_copy(
            update={"destination": GitHubPullRequestDestination(repository="owner/target")}
        )
    )
    altered_tests = (
        TRIANGLE_ASSIGNMENT.instructor_tests[0].model_copy(update={"expected": "scalene"}),
        *TRIANGLE_ASSIGNMENT.instructor_tests[1:],
    )
    tests_changed = _snapshot_sha256(
        TRIANGLE_ASSIGNMENT.model_copy(update={"instructor_tests": altered_tests})
    )
    source_changed = _snapshot_sha256(
        TRIANGLE_ASSIGNMENT.model_copy(
            update={
                "reference": TRIANGLE_ASSIGNMENT.reference.model_copy(
                    update={"source": TRIANGLE_ASSIGNMENT.reference.source + "\n# edit\n"}
                )
            }
        )
    )

    # All five (base + four mutations) are distinct: no grading-relevant field is silently ignored.
    assert len({base, domain_changed, destination_changed, tests_changed, source_changed}) == 5
