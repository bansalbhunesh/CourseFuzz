"""Test the resumable audit stream.

The audit trail is served as Server-Sent Events, and the architecture promises a client can resume
with ``Last-Event-ID`` after a dropped connection. Resumption is only trustworthy if it replays
exactly the events after the cursor and never re-delivers ones the client already saw, so this test
drives a run to a terminal stream state and checks both the full stream and a mid-stream resume.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app


def _event_ids(sse_text: str) -> list[int]:
    return [int(line[4:]) for line in sse_text.splitlines() if line.startswith("id: ")]


def test_event_stream_resumes_from_last_event_id_without_replay(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts"))

    created = client.post(
        "/api/runs",
        json={"assignment_id": "triangle-classifier"},
        headers={"Idempotency-Key": "sse-resume"},
    )
    assert created.status_code == 202
    run_id = created.json()["id"]

    # The run has reached a terminal stream state, so the stream drains and closes.
    full = client.get(f"/api/runs/{run_id}/events")
    assert full.status_code == 200
    ids = _event_ids(full.text)
    assert ids == sorted(ids)  # events are ordered
    assert len(ids) >= 3  # created + analysis + approval-required at minimum

    # Resuming after the second event replays only the later ones, never the already-seen prefix.
    cursor = ids[1]
    resumed = client.get(
        f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(cursor)}
    )
    assert resumed.status_code == 200
    resumed_ids = _event_ids(resumed.text)
    assert resumed_ids == [event_id for event_id in ids if event_id > cursor]
    assert ids[0] not in resumed_ids
    assert cursor not in resumed_ids
