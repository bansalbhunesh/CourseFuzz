"""The downloadable evidence bundle must be self-contained and independently re-hashable.

A judge downloads one JSON file and, offline, recomputes SHA-256 over its ``content`` block to
confirm the assignment snapshot, oracle provenance, approval, destination read-back receipt, and
ordered audit trail were not altered after the fact. These tests assert that the recomputed digest
matches, that it is deterministic (independent of when the bundle was produced), and that a foreign
tenant can never export another tenant's run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app
from coursefuzz.security.access import AccessPolicy

ALPHA_TOKEN = "alpha-opaque-token-at-least-24-characters"
BETA_TOKEN = "beta-opaque-token-at-least-24-characters"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Path) -> TestClient:
    policy = AccessPolicy({"alpha": ALPHA_TOKEN, "beta": BETA_TOKEN})
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts", access_policy=policy)
    return TestClient(app)


def _drive_to_verified(client: TestClient, headers: dict[str, str], key: str) -> str:
    created = client.post(
        "/api/runs",
        json={"assignment_id": "triangle-classifier"},
        headers={**headers, "Idempotency-Key": key},
    )
    assert created.status_code == 202
    run_id = created.json()["id"]
    analyzed = client.get(f"/api/runs/{run_id}", headers=headers).json()
    assert analyzed["status"] == "approval_required"
    payload_sha256 = analyzed["analysis"]["candidate"]["payload_sha256"]
    token = client.post(
        f"/api/runs/{run_id}/approval",
        json={"payload_sha256": payload_sha256},
        headers=headers,
    ).json()["approval_token"]
    applied = client.post(
        f"/api/runs/{run_id}/apply", json={"approval_token": token}, headers=headers
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "verified"
    return run_id


def _recompute(content: dict) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_evidence_bundle_is_self_contained_and_rehashes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    alpha = _auth(ALPHA_TOKEN)
    run_id = _drive_to_verified(client, alpha, "alpha-evidence")

    response = client.get(f"/api/runs/{run_id}/evidence", headers=alpha)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f"coursefuzz-evidence-{run_id}.json" in response.headers["content-disposition"]
    bundle = response.json()

    # The header advertises the same digest the body carries.
    assert response.headers["X-Evidence-SHA256"] == bundle["bundle_sha256"]

    # A third party recomputes the digest over the content block and it matches — the core claim.
    assert _recompute(bundle["content"]) == bundle["bundle_sha256"]

    content = bundle["content"]
    assert content["run"]["status"] == "verified"
    assert content["assignment_snapshot_sha256"]
    assert content["oracle_evidence"]["decision"] == "resolved"
    assert content["oracle_evidence"]["provenance"]
    assert content["artifact_sha256"]
    assert content["audit_events"], "the ordered audit trail must be embedded"
    event_types = [event["event_type"] for event in content["audit_events"]]
    # The whole loop is captured, end to end: analysis, approval, and the verified write-back.
    assert "analysis.verified" in event_types
    assert "approval.granted" in event_types
    assert "patch.verified" in event_types


def test_evidence_bundle_hash_is_deterministic_across_downloads(tmp_path: Path) -> None:
    client = _client(tmp_path)
    alpha = _auth(ALPHA_TOKEN)
    run_id = _drive_to_verified(client, alpha, "alpha-determinism")

    first = client.get(f"/api/runs/{run_id}/evidence", headers=alpha).json()
    second = client.get(f"/api/runs/{run_id}/evidence", headers=alpha).json()

    # Generation time differs, but it is envelope metadata and never enters the digest.
    assert first["bundle_sha256"] == second["bundle_sha256"]
    assert first["generated_at"] != second["generated_at"] or first["generated_at"]


def test_evidence_bundle_is_tenant_scoped(tmp_path: Path) -> None:
    client = _client(tmp_path)
    alpha = _auth(ALPHA_TOKEN)
    beta = _auth(BETA_TOKEN)
    run_id = _drive_to_verified(client, alpha, "alpha-scoped")

    assert client.get(f"/api/runs/{run_id}/evidence", headers=beta).status_code == 404
    assert client.get(f"/api/runs/{run_id}/evidence", headers=alpha).status_code == 200
