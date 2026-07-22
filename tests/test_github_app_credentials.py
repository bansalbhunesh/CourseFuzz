import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from coursefuzz.adapters.destinations import GitHubDestinationAdapter
from coursefuzz.adapters.hypotheses import DeterministicHypothesisProvider
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.data.demo import TRIANGLE_ASSIGNMENT
from coursefuzz.domain.engine import AssessmentEngine
from coursefuzz.domain.models import GitHubPullRequestDestination
from coursefuzz.repositories.sqlite import RunRepository
from coursefuzz.security.access import LOCAL_TENANT
from coursefuzz.security.github_app import GitHubAppCredentialProvider
from coursefuzz.services.assignment_service import AssignmentService
from coursefuzz.services.run_service import RunService

REPOSITORY = "course-owner/autograder"


def _decode_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_github_app_mints_repository_scoped_token_and_reuses_it_before_expiry() -> None:
    now = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.method == "POST"
        assert request.url.path == "/app/installations/314/access_tokens"
        scheme, jwt = request.headers["Authorization"].split(" ", 1)
        assert scheme == "Bearer"
        header_segment, payload_segment, signature_segment = jwt.split(".")
        assert _decode_segment(header_segment) == {"alg": "RS256", "typ": "JWT"}
        payload = _decode_segment(payload_segment)
        assert payload["iss"] == "2718"
        assert payload["exp"] - payload["iat"] == 9 * 60
        signature = base64.urlsafe_b64decode(
            signature_segment + "=" * (-len(signature_segment) % 4)
        )
        private_key.public_key().verify(
            signature,
            f"{header_segment}.{payload_segment}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        body = json.loads(request.content)
        assert body["repositories"] == ["autograder"]
        assert body["permissions"] == {
            "checks": "read",
            "contents": "write",
            "pull_requests": "write",
        }
        return httpx.Response(
            201,
            json={
                "token": "ghs_repository_scoped",
                "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        )

    provider = GitHubAppCredentialProvider(
        app_id="2718",
        private_key_pem=private_key_pem,
        installations={LOCAL_TENANT: {REPOSITORY: 314}},
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: now,
    )

    assert provider.repositories == {REPOSITORY}
    assert provider.token_for(REPOSITORY) == "ghs_repository_scoped"
    assert provider.token_for(REPOSITORY.upper()) == "ghs_repository_scoped"
    assert requests == 1


def test_github_app_fails_closed_for_uninstalled_repository() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    provider = GitHubAppCredentialProvider(
        app_id="2718",
        private_key_pem=private_key_pem,
        installations={LOCAL_TENANT: {REPOSITORY: 314}},
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(
                lambda _: pytest.fail("unknown repositories must not reach GitHub")
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="no App installation for this workspace"):
        provider.token_for("other-owner/other-course")


def test_github_app_refreshes_token_inside_expiry_safety_boundary() -> None:
    current = [datetime(2026, 7, 22, 1, 0, tzinfo=UTC)]
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            201,
            json={
                "token": f"ghs_token_{requests}",
                "expires_at": (current[0] + timedelta(hours=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        )

    provider = GitHubAppCredentialProvider(
        app_id="2718",
        private_key_pem=private_key_pem,
        installations={LOCAL_TENANT: {REPOSITORY: 314}},
        client=httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handler),
        ),
        clock=lambda: current[0],
    )

    assert provider.token_for(REPOSITORY) == "ghs_token_1"
    current[0] += timedelta(minutes=59, seconds=1)
    assert provider.token_for(REPOSITORY) == "ghs_token_2"
    assert requests == 2


def test_partial_github_app_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURSEFUZZ_GITHUB_APP_ID", "2718")
    monkeypatch.delenv("COURSEFUZZ_GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("COURSEFUZZ_GITHUB_INSTALLATIONS_JSON", raising=False)

    with pytest.raises(ValueError, match="requires app ID, private key, and installations JSON"):
        GitHubAppCredentialProvider.from_env()


def test_destination_uses_installation_token_for_exact_repository() -> None:
    now = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/installations/314/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": "ghs_exact_repository",
                    "expires_at": (now + timedelta(hours=1))
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
        assert request.url.path == f"/repos/{REPOSITORY}/commits/{'a' * 40}/check-runs"
        assert request.headers["Authorization"] == "Bearer ghs_exact_repository"
        return httpx.Response(
            200,
            json={
                "check_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.test/check/1",
                    }
                ]
            },
        )

    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handler),
    )
    provider = GitHubAppCredentialProvider(
        app_id="2718",
        private_key_pem=private_key_pem,
        installations={LOCAL_TENANT: {REPOSITORY: 314}},
        client=client,
        clock=lambda: now,
    )
    adapter = GitHubDestinationAdapter(client=client, credential_provider=provider)

    result = adapter.check_runs(REPOSITORY, "a" * 40)

    assert adapter.available is True
    assert adapter.allowed_repositories == {REPOSITORY}
    assert adapter.credential_mode == "github-app"
    assert result.state == "success"


def test_destination_rejects_repository_owned_by_another_workspace() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    client = httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(
            lambda _: pytest.fail("cross-workspace target must not reach GitHub")
        ),
    )
    provider = GitHubAppCredentialProvider(
        app_id="2718",
        private_key_pem=private_key_pem,
        installations={
            "alpha": {REPOSITORY: 314},
            "beta": {"course-owner/other-course": 271},
        },
        client=client,
    )
    adapter = GitHubDestinationAdapter(client=client, credential_provider=provider)

    assert adapter.repository_available(REPOSITORY, "alpha") is True
    assert adapter.repository_available(REPOSITORY, "beta") is False
    with pytest.raises(RuntimeError, match="no App installation for this workspace"):
        provider.token_for(REPOSITORY, "beta")


def test_run_creation_rejects_cross_workspace_repository_before_analysis(
    tmp_path: Path,
) -> None:
    class WorkspaceScopedGitHub:
        available = True
        credential_mode = "github-app"

        @staticmethod
        def repository_available(repository: str, tenant_id: str) -> bool:
            return repository == REPOSITORY and tenant_id == "alpha"

    class WorkspaceScopedDestinations:
        github = WorkspaceScopedGitHub()

    repository = RunRepository(tmp_path / "coursefuzz.db")
    sandbox = SubprocessPythonSandbox()
    assignments = AssignmentService(repository, sandbox)
    assignments.seed(
        TRIANGLE_ASSIGNMENT.model_copy(
            update={
                "id": "tenant-github",
                "destination": GitHubPullRequestDestination(repository=REPOSITORY),
            }
        )
    )
    service = RunService(
        repository,
        AssessmentEngine(sandbox, DeterministicHypothesisProvider()),
        assignments,
        tmp_path / "artifacts",
        "deterministic-fallback",
        WorkspaceScopedDestinations(),  # type: ignore[arg-type]
    )

    allowed, created = service.create_run("tenant-github", "alpha-run", "alpha")
    assert created is True
    assert allowed.status == "queued"
    with pytest.raises(ValueError, match="not configured or authorized for this workspace"):
        service.create_run("tenant-github", "beta-run", "beta")
