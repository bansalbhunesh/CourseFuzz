from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.main import create_app
from coursefuzz.security.access import JUDGE_TENANT, AccessPolicy

ALPHA_TOKEN = "alpha-opaque-token-at-least-24-characters"
BETA_TOKEN = "beta-opaque-token-at-least-24-characters"


def authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assignment_payload() -> dict:
    assignment = TRIANGLE_ASSIGNMENT
    return {
        "title": "Alpha tenant triangle suite",
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
            {"title": item.title, "source": item.source}
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


def build_client(tmp_path: Path) -> TestClient:
    policy = AccessPolicy({"alpha": ALPHA_TOKEN, "beta": BETA_TOKEN})
    app = create_app(
        tmp_path / "coursefuzz.db",
        tmp_path / "artifacts",
        access_policy=policy,
    )
    return TestClient(app)


def test_protected_api_requires_a_valid_credential(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    assert client.get("/api/health").json()["auth"] == "required"
    assert client.get("/api/health").json()["github_auth"] == "unconfigured"
    missing = client.get("/api/assignments")
    invalid = client.get(
        "/api/assignments", headers=authorization("not-a-real-coursefuzz-token")
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert invalid.status_code == 401


def test_health_reports_github_auth_mode_without_exposing_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "github-token-that-must-never-leave-the-server"
    monkeypatch.setenv("COURSEFUZZ_GITHUB_TOKEN", token)
    monkeypatch.setenv("COURSEFUZZ_GITHUB_ALLOWED_REPOS", "course-owner/autograder")
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts")
    health = TestClient(app).get("/api/health")

    assert health.status_code == 200
    assert health.json()["github_destination"] == "configured"
    assert health.json()["github_auth"] == "static-token"
    assert token not in health.text


def test_assignments_runs_and_approvals_are_tenant_scoped(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    alpha = authorization(ALPHA_TOKEN)
    beta = authorization(BETA_TOKEN)

    created = client.post("/api/assignments", json=assignment_payload(), headers=alpha)
    assert created.status_code == 201
    assignment_id = created.json()["id"]
    assert client.get(f"/api/assignments/{assignment_id}", headers=beta).status_code == 404

    run = client.post(
        "/api/runs",
        json={"assignment_id": assignment_id},
        headers={**alpha, "Idempotency-Key": "tenant-bound-run"},
    )
    assert run.status_code == 202
    run_id = run.json()["id"]

    assert client.get(f"/api/runs/{run_id}", headers=beta).status_code == 404
    assert (
        client.post(
            f"/api/runs/{run_id}/approval",
            json={"payload_sha256": "0" * 64},
            headers=beta,
        ).status_code
        == 404
    )
    assert client.get(f"/api/runs/{run_id}/events", headers=beta).status_code == 404
    assert client.get(f"/api/runs/{run_id}", headers=alpha).status_code == 200


def test_browser_session_uses_an_httponly_cookie(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("COURSEFUZZ_COOKIE_SECURE", "0")
    client = build_client(tmp_path)

    signed_in = client.post("/api/session", json={"access_token": ALPHA_TOKEN})

    assert signed_in.status_code == 200
    assert signed_in.json() == {"tenant_id": "alpha"}
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert "SameSite=strict" in signed_in.headers["set-cookie"]
    assert client.get("/api/assignments").status_code == 200

    signed_out = client.delete("/api/session")
    assert signed_out.status_code == 204
    assert client.get("/api/assignments").status_code == 401


def test_independent_judge_credential_does_not_replace_owner_keys(
    monkeypatch,
) -> None:
    judge_token = "judge-review-token-at-least-24-characters"
    monkeypatch.setenv("COURSEFUZZ_ACCESS_KEYS_JSON", f'{{"alpha":"{ALPHA_TOKEN}"}}')
    monkeypatch.setenv("COURSEFUZZ_JUDGE_ACCESS_TOKEN", judge_token)

    policy = AccessPolicy.from_env()

    assert policy.authenticate(f"Bearer {ALPHA_TOKEN}").tenant_id == "alpha"
    assert policy.authenticate(f"Bearer {judge_token}").tenant_id == JUDGE_TENANT


def test_conflicting_judge_tenant_configuration_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv(
        "COURSEFUZZ_ACCESS_KEYS_JSON",
        '{"judge-review":"one-judge-token-at-least-24-characters"}',
    )
    monkeypatch.setenv(
        "COURSEFUZZ_JUDGE_ACCESS_TOKEN",
        "different-judge-token-at-least-24-characters",
    )

    with pytest.raises(ValueError, match="configured differently"):
        AccessPolicy.from_env()
