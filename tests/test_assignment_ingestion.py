from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.main import create_app


def build_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts"))


def triangle_payload() -> dict:
    assignment = TRIANGLE_ASSIGNMENT
    return {
        "title": "Triangle classifier imported by an instructor",
        "summary": assignment.summary,
        "entrypoint": assignment.entrypoint,
        "input_names": list(assignment.input_names),
        "domain_min": assignment.domain_min,
        "domain_max": assignment.domain_max,
        "reference": {
            "title": assignment.reference.title,
            "source": assignment.reference.source,
        },
        "accepted_solutions": [
            {
                "title": item.title,
                "source": item.source,
            }
            for item in assignment.accepted_solutions
            if item.id != assignment.reference.id
        ],
        "misconception_programs": [
            {
                "title": item.title,
                "misconception": item.misconception,
                "source": item.source,
            }
            for item in assignment.mutants
        ],
        "instructor_tests": [
            {
                "inputs": list(item.inputs),
                "expected": item.expected,
                "label": item.label,
            }
            for item in assignment.instructor_tests
        ],
    }


def test_manual_assignment_is_content_addressed_deduplicated_and_runnable(
    tmp_path: Path,
) -> None:
    client = build_client(tmp_path)
    payload = triangle_payload()

    created = client.post("/api/assignments", json=payload)
    duplicate = client.post("/api/assignments", json=payload)

    assert created.status_code == 201
    assert duplicate.status_code == 200
    assert created.json()["id"].startswith("asg_")
    assert duplicate.json()["id"] == created.json()["id"]
    assert duplicate.json()["snapshot_sha256"] == created.json()["snapshot_sha256"]

    assignment_id = created.json()["id"]
    detail = client.get(f"/api/assignments/{assignment_id}")
    assert detail.status_code == 200
    assert detail.json()["spec"]["title"] == payload["title"]

    run = client.post(
        "/api/runs",
        json={"assignment_id": assignment_id},
        headers={"Idempotency-Key": "manual-assignment-run"},
    )
    assert run.status_code == 202
    analyzed = client.get(f"/api/runs/{run.json()['id']}").json()
    assert analyzed["assignment_snapshot_sha256"] == created.json()["snapshot_sha256"]
    assert analyzed["status"] == "approval_required"


def test_assignment_preflight_rejects_a_broken_accepted_control(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    payload = triangle_payload()
    payload["accepted_solutions"][0]["source"] = (
        "def classify_triangle(a, b, c):\n    return 'invalid'\n"
    )

    response = client.post("/api/assignments", json=payload)

    assert response.status_code == 422
    assert "accepted control" in response.json()["detail"]


def test_assignment_preflight_rejects_byte_identical_controls(tmp_path: Path) -> None:
    """The oracle's guarantee is that two *independent* controls agree. Two byte-identical
    controls would make that agreement trivial and meaningless, so ingestion must refuse them.
    """

    client = build_client(tmp_path)
    payload = triangle_payload()
    # Make a supposedly independent accepted control identical to the reference source.
    payload["accepted_solutions"][0]["source"] = payload["reference"]["source"]

    response = client.post("/api/assignments", json=payload)

    assert response.status_code == 422
    assert "distinct source code" in response.json()["detail"]
