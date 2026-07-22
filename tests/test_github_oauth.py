import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from coursefuzz.main import create_app
from coursefuzz.security.access import AccessPolicy
from coursefuzz.security.github_oauth import (
    GitHubOAuthClient,
    sign_state,
    verify_state,
)

SECRET = "oauth-client-secret-value"
ACME_TOKEN = "acme-opaque-token-at-least-24-characters"
WEBHOOK_SECRET = "webhook-signing-secret-value"


def test_state_roundtrip_and_tamper() -> None:
    token = sign_state({"tenant_id": "acme", "installation_id": 5, "iat": int(time.time())}, SECRET)
    payload = verify_state(token, SECRET)
    assert (
        payload is not None and payload["tenant_id"] == "acme" and payload["installation_id"] == 5
    )

    body, _, sig = token.partition(".")
    assert verify_state(f"{body}.deadbeef", SECRET) is None  # bad signature
    assert verify_state(token, "different-secret") is None  # wrong key
    assert verify_state("garbage", SECRET) is None


def test_state_expiry() -> None:
    old = sign_state(
        {"tenant_id": "acme", "installation_id": 5, "iat": int(time.time()) - 10_000}, SECRET
    )
    assert verify_state(old, SECRET) is None
    future = sign_state(
        {"tenant_id": "acme", "installation_id": 5, "iat": int(time.time()) + 5_000}, SECRET
    )
    assert verify_state(future, SECRET) is None  # issued implausibly in the future


def _oauth(oauth_handler, api_handler) -> GitHubOAuthClient:
    return GitHubOAuthClient(
        client_id="Iv1.client",
        client_secret=SECRET,
        oauth_client=httpx.Client(
            base_url="https://github.com", transport=httpx.MockTransport(oauth_handler)
        ),
        api_client=httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(api_handler)
        ),
    )


def test_authorize_url_contains_client_and_state() -> None:
    client = _oauth(lambda r: httpx.Response(200), lambda r: httpx.Response(200))
    url = client.authorize_url(state="STATE123", redirect_uri="https://app/api/github/callback")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Iv1.client" in url and "state=STATE123" in url


def test_exchange_code_and_user_installations() -> None:
    def oauth_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(200, json={"access_token": "u_token", "token_type": "bearer"})

    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/installations"
        assert request.headers["Authorization"] == "Bearer u_token"
        return httpx.Response(
            200, json={"total_count": 2, "installations": [{"id": 11}, {"id": 22}]}
        )

    client = _oauth(oauth_handler, api_handler)
    token = client.exchange_code(code="abc", redirect_uri="https://app/api/github/callback")
    assert token == "u_token"
    assert client.user_installation_ids(token) == frozenset({11, 22})


# --- Full login -> callback flow through the app -------------------------------------------------


def _sign_webhook(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _client(tmp_path: Path, monkeypatch, oauth_client: GitHubOAuthClient) -> TestClient:
    monkeypatch.setenv("COURSEFUZZ_GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("COURSEFUZZ_GITHUB_OAUTH_REDIRECT_URI", "https://app/api/github/callback")
    monkeypatch.setattr(GitHubOAuthClient, "from_env", lambda **kw: oauth_client)
    policy = AccessPolicy({"acme": ACME_TOKEN})
    app = create_app(tmp_path / "cf.db", tmp_path / "art", access_policy=policy)
    return TestClient(app)


def _onboard(client: TestClient, installation_id: int, repos: list[str]) -> None:
    body = json.dumps(
        {
            "action": "created",
            "installation": {"id": installation_id, "account": {"login": "acme"}},
            "repositories": [{"full_name": r} for r in repos],
        }
    ).encode()
    client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign_webhook(body),
            "X-GitHub-Event": "installation",
            "X-GitHub-Delivery": f"deliver-{installation_id}",
        },
    )


def test_login_redirects_to_github(tmp_path: Path, monkeypatch) -> None:
    oauth = _oauth(lambda r: httpx.Response(200), lambda r: httpx.Response(200))
    client = _client(tmp_path, monkeypatch, oauth)
    resp = client.get(
        "/api/github/login",
        params={"installation_id": 4242},
        headers={"Authorization": f"Bearer {ACME_TOKEN}"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "state=" in location

    # Unauthenticated login is rejected.
    assert client.get("/api/github/login", params={"installation_id": 4242}).status_code == 401


def test_callback_binds_when_user_owns_installation(tmp_path: Path, monkeypatch) -> None:
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"installations": [{"id": 4242}]})

    oauth = _oauth(lambda r: httpx.Response(200, json={"access_token": "u_token"}), api_handler)
    client = _client(tmp_path, monkeypatch, oauth)
    _onboard(client, 4242, ["acme/cs101"])

    state = sign_state(
        {"tenant_id": "acme", "installation_id": 4242, "iat": int(time.time()), "nonce": "n"},
        SECRET,
    )
    resp = client.get(
        "/api/github/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?github=connected"

    listed = client.get(
        "/api/github/repositories", headers={"Authorization": f"Bearer {ACME_TOKEN}"}
    )
    assert listed.json() == {"installation_id": 4242, "repositories": ["acme/cs101"]}


def test_callback_denies_when_user_does_not_own_installation(tmp_path: Path, monkeypatch) -> None:
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"installations": [{"id": 9999}]})  # not 4242

    oauth = _oauth(lambda r: httpx.Response(200, json={"access_token": "u_token"}), api_handler)
    client = _client(tmp_path, monkeypatch, oauth)
    _onboard(client, 4242, ["acme/cs101"])

    state = sign_state(
        {"tenant_id": "acme", "installation_id": 4242, "iat": int(time.time()), "nonce": "n"},
        SECRET,
    )
    resp = client.get(
        "/api/github/callback",
        params={"code": "the-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?github=denied"
    # Nothing was bound.
    listed = client.get(
        "/api/github/repositories", headers={"Authorization": f"Bearer {ACME_TOKEN}"}
    )
    assert listed.json()["repositories"] == []


def test_callback_rejects_forged_state(tmp_path: Path, monkeypatch) -> None:
    oauth = _oauth(
        lambda r: httpx.Response(200, json={"access_token": "u_token"}),
        lambda r: httpx.Response(200, json={"installations": [{"id": 4242}]}),
    )
    client = _client(tmp_path, monkeypatch, oauth)
    _onboard(client, 4242, ["acme/cs101"])

    forged = sign_state(
        {"tenant_id": "acme", "installation_id": 4242, "iat": int(time.time())},
        "attacker-secret",
    )
    resp = client.get(
        "/api/github/callback",
        params={"code": "the-code", "state": forged},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/?github=error"
