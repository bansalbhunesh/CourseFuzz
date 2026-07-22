import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from coursefuzz.main import create_app
from coursefuzz.security.access import AccessPolicy

WEBHOOK_SECRET = "webhook-signing-secret-value"
ACME_TOKEN = "acme-opaque-token-at-least-24-characters"
BETA_TOKEN = "beta-opaque-token-at-least-24-characters"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Path, monkeypatch, *, claim_enabled: bool = False) -> TestClient:
    monkeypatch.setenv("COURSEFUZZ_GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("COURSEFUZZ_ENABLE_SELF_SERVE_CLAIM", "1" if claim_enabled else "0")
    policy = AccessPolicy({"acme": ACME_TOKEN, "beta": BETA_TOKEN})
    app = create_app(tmp_path / "coursefuzz.db", tmp_path / "artifacts", access_policy=policy)
    return TestClient(app)


def _deliver(
    client: TestClient, event: str, payload: dict, delivery: str, *, secret: str = WEBHOOK_SECRET
):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "Content-Type": "application/json",
        },
    )


def _created_payload(installation_id: int, repos: list[str], account: str = "acme") -> dict:
    return {
        "action": "created",
        "installation": {"id": installation_id, "account": {"login": account}},
        "repositories": [{"full_name": r} for r in repos],
    }


def test_signed_webhook_ingests_and_dedupes(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = _created_payload(148_000, ["acme/cs101-autograder"])

    first = _deliver(client, "installation", payload, "delivery-A")
    assert first.status_code == 202 and first.json()["status"] == "applied"

    duplicate = _deliver(client, "installation", payload, "delivery-A")
    assert duplicate.status_code == 202 and duplicate.json()["status"] == "duplicate"


def test_bad_signature_is_rejected(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = _created_payload(148_001, ["acme/course"])
    bad = _deliver(client, "installation", payload, "delivery-B", secret="wrong-secret")
    assert bad.status_code == 401


def test_unrecognized_event_is_ignored(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = _deliver(client, "push", {"ref": "refs/heads/main"}, "delivery-C")
    assert resp.status_code == 202 and resp.json()["status"] == "ignored"


def test_repository_picker_requires_auth_and_is_tenant_scoped(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, claim_enabled=True)
    _deliver(client, "installation", _created_payload(148_002, ["acme/cs101"]), "delivery-D")

    assert client.get("/api/github/repositories").status_code == 401

    # Before claiming, the workspace sees no repositories.
    empty = client.get("/api/github/repositories", headers=_auth(ACME_TOKEN))
    assert empty.status_code == 200 and empty.json()["repositories"] == []

    claim = client.post(
        "/api/github/installations/claim",
        json={"installation_id": 148_002},
        headers=_auth(ACME_TOKEN),
    )
    assert claim.status_code == 200
    assert claim.json()["repositories"] == ["acme/cs101"]

    listed = client.get("/api/github/repositories", headers=_auth(ACME_TOKEN))
    assert listed.json() == {"installation_id": 148_002, "repositories": ["acme/cs101"]}

    # A different workspace cannot see or re-claim another workspace's installation.
    other = client.get("/api/github/repositories", headers=_auth(BETA_TOKEN))
    assert other.json()["repositories"] == []
    stolen = client.post(
        "/api/github/installations/claim",
        json={"installation_id": 148_002},
        headers=_auth(BETA_TOKEN),
    )
    assert stolen.status_code == 409


def test_claim_is_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, claim_enabled=False)
    _deliver(client, "installation", _created_payload(148_003, ["acme/course"]), "delivery-E")
    disabled = client.post(
        "/api/github/installations/claim",
        json={"installation_id": 148_003},
        headers=_auth(ACME_TOKEN),
    )
    assert disabled.status_code == 404


def test_removed_repository_disappears_from_picker(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, claim_enabled=True)
    _deliver(
        client,
        "installation",
        _created_payload(148_004, ["acme/course-a", "acme/course-b"]),
        "delivery-F",
    )
    client.post(
        "/api/github/installations/claim",
        json={"installation_id": 148_004},
        headers=_auth(ACME_TOKEN),
    )
    _deliver(
        client,
        "installation_repositories",
        {
            "action": "removed",
            "installation": {"id": 148_004, "account": {"login": "acme"}},
            "repositories_removed": [{"full_name": "acme/course-a"}],
        },
        "delivery-G",
    )
    listed = client.get("/api/github/repositories", headers=_auth(ACME_TOKEN))
    assert listed.json()["repositories"] == ["acme/course-b"]
