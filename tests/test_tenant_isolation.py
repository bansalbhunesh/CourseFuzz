"""Adversarial tests for cross-tenant isolation on the mutating and data-egress paths.

Existing coverage proves one tenant cannot *read* another's assignment, run, or event stream. These
tests extend the guarantee to the paths that change state or export data, which are the ones an
attacker actually wants: applying a repair, downloading the generated artifact, and colliding
idempotency keys. A valid credential for tenant beta must never reach tenant alpha's run — even when
beta somehow holds alpha's real approval token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
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


def test_apply_and_artifact_are_isolated_even_with_a_leaked_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    alpha = _auth(ALPHA_TOKEN)
    beta = _auth(BETA_TOKEN)

    # Alpha drives a run to the approved boundary on the shared seeded assignment.
    created = client.post(
        "/api/runs",
        json={"assignment_id": "triangle-classifier"},
        headers={**alpha, "Idempotency-Key": "alpha-isolation"},
    )
    assert created.status_code == 202
    run_id = created.json()["id"]
    analyzed = client.get(f"/api/runs/{run_id}", headers=alpha).json()
    assert analyzed["status"] == "approval_required"
    payload_sha256 = analyzed["analysis"]["candidate"]["payload_sha256"]
    token = client.post(
        f"/api/runs/{run_id}/approval",
        json={"payload_sha256": payload_sha256},
        headers=alpha,
    ).json()["approval_token"]

    # Beta cannot apply alpha's run, even holding alpha's genuine approval token...
    assert (
        client.post(
            f"/api/runs/{run_id}/apply", json={"approval_token": token}, headers=beta
        ).status_code
        == 404
    )
    # ...nor with a forged one. Either way the run is untouched and alpha can still apply it.
    assert (
        client.post(
            f"/api/runs/{run_id}/apply", json={"approval_token": "forged"}, headers=beta
        ).status_code
        == 404
    )
    assert client.get(f"/api/runs/{run_id}", headers=alpha).json()["status"] == "approved"

    applied = client.post(
        f"/api/runs/{run_id}/apply", json={"approval_token": token}, headers=alpha
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "verified"

    # The verified artifact is downloadable by its owner and invisible to beta.
    beta_artifact = client.get(f"/api/runs/{run_id}/artifact", headers=beta)
    assert beta_artifact.status_code == 404

    alpha_artifact = client.get(f"/api/runs/{run_id}/artifact", headers=alpha)
    assert alpha_artifact.status_code == 200
    assert "X-Artifact-SHA256" in alpha_artifact.headers
    assert "def test_coursefuzz_classify_triangle" in alpha_artifact.text


def test_idempotency_keys_do_not_collide_across_tenants(tmp_path: Path) -> None:
    service = create_app(
        tmp_path / "coursefuzz.db",
        tmp_path / "artifacts",
        access_policy=AccessPolicy({"alpha": ALPHA_TOKEN, "beta": BETA_TOKEN}),
    ).state.run_service

    alpha_run, alpha_created = service.create_run("triangle-classifier", "shared-key", "alpha")
    beta_run, beta_created = service.create_run("triangle-classifier", "shared-key", "beta")

    # The same key in two tenants yields two independent runs; it is not a collision or a takeover.
    assert alpha_created and beta_created
    assert alpha_run.id != beta_run.id

    # Reusing the key within the same tenant is idempotent and returns the same run.
    alpha_again, created_again = service.create_run("triangle-classifier", "shared-key", "alpha")
    assert not created_again
    assert alpha_again.id == alpha_run.id

    # Ownership holds: neither tenant can resolve the other's run by id.
    assert service.require_run(alpha_run.id, "alpha").id == alpha_run.id
    with pytest.raises(KeyError):
        service.require_run(alpha_run.id, "beta")
    with pytest.raises(KeyError):
        service.require_run(beta_run.id, "alpha")
