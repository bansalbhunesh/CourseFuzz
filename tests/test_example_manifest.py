import json
from pathlib import Path

from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_GITHUB_ASSIGNMENT
from coursefuzz.domain.models import AssignmentCreate, GitHubPullRequestDestination
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.services.assignment_service import AssignmentService

ROOT = Path(__file__).resolve().parents[1]


def test_github_demo_manifest_is_importable_and_targets_only_demo_repository(
    tmp_path: Path,
) -> None:
    raw = json.loads(
        (ROOT / "examples" / "github-demo-assignment.json").read_text(encoding="utf-8")
    )
    payload = AssignmentCreate.model_validate(raw)
    service = AssignmentService(
        RunRepository(tmp_path / "coursefuzz.db"),
        SubprocessPythonSandbox(),
    )

    snapshot, created = service.create(payload, "judge-review")

    assert created is True
    assert len(snapshot.spec.accepted_solutions) == 2
    assert len(snapshot.spec.mutants) == 8
    assert isinstance(snapshot.spec.destination, GitHubPullRequestDestination)
    assert snapshot.spec.destination.repository == "bansalbhunesh/CourseFuzz-Demo-Target"
    assert snapshot.spec.destination.base_branch == "main"
    assert snapshot.spec.destination.test_directory == "tests/coursefuzz"
    assert TRIANGLE_GITHUB_ASSIGNMENT.destination == snapshot.spec.destination
    assert TRIANGLE_GITHUB_ASSIGNMENT.title == payload.title
