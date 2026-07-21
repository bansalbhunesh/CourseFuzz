from pathlib import Path

from scripts.release_guard import check_release

ROOT = Path(__file__).resolve().parents[1]


def test_repository_release_evidence_is_internally_consistent() -> None:
    assert check_release(ROOT) == []


def test_submission_guard_keeps_missing_public_proof_as_a_blocker() -> None:
    failures = check_release(ROOT, require_submission=True)

    assert "release status is not submission-ready" in failures
    assert not any("public_demo_url" in failure for failure in failures)
    assert any("video_url" in failure for failure in failures)
    assert any("live_github_receipt_url" in failure for failure in failures)
