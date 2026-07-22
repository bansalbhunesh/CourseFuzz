from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from coursefuzz.security.github_app import (
    CompositeGitHubCredentialProvider,
    StoredGitHubCredentialProvider,
)
from coursefuzz.security.installations import (
    SqliteInstallationStore,
    apply_installation_event,
)
from coursefuzz.security.webhooks import parse_installation_event

NOW = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _onboard(store: SqliteInstallationStore, installation_id: int, repos: list[str]) -> None:
    event = parse_installation_event(
        "installation",
        {
            "action": "created",
            "installation": {"id": installation_id, "account": {"login": "acme"}},
            "repositories": [{"full_name": r} for r in repos],
        },
    )
    assert event is not None
    apply_installation_event(store, event)


def _no_mint_client() -> httpx.Client:
    def reject(_: httpx.Request) -> httpx.Response:
        raise AssertionError("token must not be minted")

    return httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(reject))


def _client(installation_id: int) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/app/installations/{installation_id}/access_tokens"
        return httpx.Response(
            201,
            json={
                "token": "ghs_stored_scoped",
                "expires_at": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        )

    return httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(handler))


def test_stored_provider_mints_for_onboarded_bound_repository(tmp_path: Path) -> None:
    store = SqliteInstallationStore(tmp_path / "s.db")
    _onboard(store, 900, ["acme/cs101-autograder"])
    store.bind_workspace("acme-team", 900)
    provider = StoredGitHubCredentialProvider(
        app_id="2718", private_key_pem=_pem(), store=store, client=_client(900), clock=lambda: NOW
    )

    assert provider.mode == "github-app"
    assert provider.allows("acme/cs101-autograder", "acme-team") is True
    assert provider.token_for("acme/cs101-autograder", "acme-team") == "ghs_stored_scoped"
    # Case-insensitive repository match.
    assert provider.allows("ACME/CS101-Autograder", "acme-team") is True


def test_stored_provider_fails_closed_for_unbound_tenant(tmp_path: Path) -> None:
    store = SqliteInstallationStore(tmp_path / "s.db")
    _onboard(store, 901, ["acme/course"])  # onboarded but no workspace binding
    provider = StoredGitHubCredentialProvider(
        app_id="2718",
        private_key_pem=_pem(),
        store=store,
        client=_no_mint_client(),
        clock=lambda: NOW,
    )
    assert provider.allows("acme/course", "acme-team") is False
    assert provider.token_for("acme/course", "acme-team") is None


def test_stored_provider_isolates_repositories_across_tenants(tmp_path: Path) -> None:
    store = SqliteInstallationStore(tmp_path / "s.db")
    _onboard(store, 902, ["acme/course-a"])
    _onboard(store, 903, ["beta/course-b"])
    store.bind_workspace("acme-team", 902)
    store.bind_workspace("beta-team", 903)
    provider = StoredGitHubCredentialProvider(
        app_id="2718",
        private_key_pem=_pem(),
        store=store,
        client=_no_mint_client(),
        clock=lambda: NOW,
    )
    # Each tenant is confined to its own installation's repositories.
    assert provider.allows("beta/course-b", "acme-team") is False
    assert provider.allows("acme/course-a", "beta-team") is False
    assert provider.token_for("beta/course-b", "acme-team") is None


def test_stored_provider_fails_closed_after_suspend(tmp_path: Path) -> None:
    store = SqliteInstallationStore(tmp_path / "s.db")
    _onboard(store, 904, ["acme/course"])
    store.bind_workspace("acme-team", 904)
    event = parse_installation_event(
        "installation", {"action": "suspend", "installation": {"id": 904}}
    )
    assert event is not None
    apply_installation_event(store, event)
    provider = StoredGitHubCredentialProvider(
        app_id="2718",
        private_key_pem=_pem(),
        store=store,
        client=_no_mint_client(),
        clock=lambda: NOW,
    )
    assert provider.allows("acme/course", "acme-team") is False


def test_composite_prefers_stored_then_static(tmp_path: Path) -> None:
    store = SqliteInstallationStore(tmp_path / "s.db")
    _onboard(store, 905, ["acme/self-serve"])
    store.bind_workspace("acme-team", 905)
    stored = StoredGitHubCredentialProvider(
        app_id="2718", private_key_pem=_pem(), store=store, client=_client(905), clock=lambda: NOW
    )

    class _StaticStub:
        available = True
        repositories = frozenset({"demo-owner/demo-target"})
        mode = "github-app"

        def allows(self, repository: str, tenant_id: str = "local-demo") -> bool:
            return repository.lower() == "demo-owner/demo-target" and tenant_id == "judge-review"

        def token_for(self, repository: str, tenant_id: str = "local-demo") -> str:
            return "ghs_static_demo"

    composite = CompositeGitHubCredentialProvider([stored, _StaticStub()])
    assert composite.mode == "github-app"
    # Self-serve tenant resolves through the stored provider.
    assert composite.token_for("acme/self-serve", "acme-team") == "ghs_stored_scoped"
    # Demo/judge path still resolves through the static provider, unchanged.
    assert composite.token_for("demo-owner/demo-target", "judge-review") == "ghs_static_demo"
    # A repository nobody authorizes yields no token.
    assert composite.token_for("stranger/repo", "acme-team") is None
    assert "demo-owner/demo-target" in composite.repositories
