from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app


def build_client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    return TestClient(app)


def test_golden_path_is_idempotent_approved_applied_and_read_back(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    headers = {"Idempotency-Key": "golden-demo-1"}

    first = client.post("/api/runs", json={}, headers=headers)
    duplicate = client.post("/api/runs", json={}, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    run_id = first.json()["id"]

    analyzed = client.get(f"/api/runs/{run_id}").json()
    assert analyzed["status"] == "approval_required"
    assert analyzed["analysis"]["candidate"]["test"]["inputs"] == [1, 2, 2]
    payload_sha256 = analyzed["analysis"]["candidate"]["payload_sha256"]

    wrong_approval = client.post(f"/api/runs/{run_id}/approval", json={"payload_sha256": "0" * 64})
    assert wrong_approval.status_code == 409

    receipt = client.post(
        f"/api/runs/{run_id}/approval",
        json={"payload_sha256": payload_sha256},
    )
    assert receipt.status_code == 200

    applied = client.post(
        f"/api/runs/{run_id}/apply",
        json={"approval_token": receipt.json()["approval_token"]},
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "verified"
    assert applied.json()["artifact_sha256"]
    assert applied.json()["action_receipt"]["read_back_verified"] is True
    assert applied.json()["action_receipt"]["kind"] == "local_artifact"

    artifact = client.get(f"/api/runs/{run_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.headers["x-artifact-sha256"] == applied.json()["artifact_sha256"]
    assert "assert classify_triangle(1, 2, 2) == 'isosceles'" in artifact.text

    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert "event: approval.granted" in events.text
    assert "event: patch.verified" in events.text


def test_sse_reconnect_resumes_after_last_event_id(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    response = client.post("/api/runs", json={}, headers={"Idempotency-Key": "sse-reconnect"})
    run_id = response.json()["id"]

    all_events = client.get(f"/api/runs/{run_id}/events").text
    assert "id: 1\n" in all_events

    resumed = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"}).text
    assert "id: 1\n" not in resumed
    assert "event: analysis.started" in resumed
