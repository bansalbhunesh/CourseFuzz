from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from coursefuzz.security.access import LOCAL_TENANT

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


class GitHubCredentialProvider(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def repositories(self) -> frozenset[str]: ...

    @property
    def mode(self) -> str: ...

    def allows(self, repository: str, tenant_id: str = LOCAL_TENANT) -> bool: ...

    def token_for(self, repository: str, tenant_id: str = LOCAL_TENANT) -> str | None: ...


@dataclass(frozen=True, slots=True)
class StaticGitHubCredentialProvider:
    """Backward-compatible deploy credential for the single-repository beta."""

    token: str | None

    @property
    def available(self) -> bool:
        return bool(self.token)

    @property
    def repositories(self) -> frozenset[str]:
        return frozenset()

    @property
    def mode(self) -> str:
        return "static-token" if self.available else "unconfigured"

    def allows(self, repository: str, tenant_id: str = LOCAL_TENANT) -> bool:
        del repository, tenant_id
        return True

    def token_for(self, repository: str, tenant_id: str = LOCAL_TENANT) -> str | None:
        del repository
        del tenant_id
        return self.token


@dataclass(frozen=True, slots=True)
class _CachedInstallationToken:
    token: str
    expires_at: datetime


class GitHubAppCredentialProvider:
    """Mint short-lived, repository-scoped GitHub App installation tokens.

    Repository-to-installation mapping is explicit and fail-closed. Tokens are cached only for the
    exact repository they were minted for and are refreshed before GitHub's expiry boundary.
    """

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        installations: Mapping[str, Mapping[str, int]],
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not app_id.isdigit() or int(app_id) <= 0:
            raise ValueError("COURSEFUZZ_GITHUB_APP_ID must be a positive integer")
        if not installations:
            raise ValueError("At least one GitHub App repository installation is required")
        normalized: dict[str, dict[str, int]] = {}
        for tenant_id, repository_map in installations.items():
            if not _TENANT_PATTERN.fullmatch(tenant_id):
                raise ValueError(f"Invalid CourseFuzz tenant ID: {tenant_id!r}")
            if not isinstance(repository_map, Mapping) or not repository_map:
                raise ValueError("Every GitHub App tenant must map at least one repository")
            normalized_repositories: dict[str, int] = {}
            for repository, installation_id in repository_map.items():
                if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
                    raise ValueError(f"Invalid GitHub repository: {repository!r}")
                if not isinstance(installation_id, int) or isinstance(installation_id, bool):
                    raise ValueError("GitHub installation IDs must be integers")
                if installation_id <= 0:
                    raise ValueError("GitHub installation IDs must be positive")
                normalized_repositories[repository.lower()] = installation_id
            normalized[tenant_id] = normalized_repositories

        decoded_key = private_key_pem.replace("\\n", "\n").encode("utf-8")
        try:
            private_key = serialization.load_pem_private_key(decoded_key, password=None)
        except (TypeError, ValueError) as exc:
            raise ValueError("COURSEFUZZ_GITHUB_APP_PRIVATE_KEY is not a valid PEM key") from exc
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError("GitHub App authentication requires an RSA private key")

        self._app_id = app_id
        self._private_key = private_key
        self._installations = normalized
        self._client = client or httpx.Client(
            base_url="https://api.github.com",
            timeout=10.0,
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[tuple[int, str], _CachedInstallationToken] = {}
        self._cache_lock = Lock()

    @classmethod
    def from_env(
        cls,
        *,
        client: httpx.Client | None = None,
    ) -> GitHubAppCredentialProvider | None:
        app_id = os.getenv("COURSEFUZZ_GITHUB_APP_ID", "").strip()
        private_key = os.getenv("COURSEFUZZ_GITHUB_APP_PRIVATE_KEY", "").strip()
        raw_installations = os.getenv("COURSEFUZZ_GITHUB_INSTALLATIONS_JSON", "").strip()
        configured = (bool(app_id), bool(private_key), bool(raw_installations))
        if not any(configured):
            return None
        if not all(configured):
            raise ValueError(
                "GitHub App configuration requires app ID, private key, and installations JSON"
            )
        try:
            parsed = json.loads(raw_installations)
        except json.JSONDecodeError as exc:
            raise ValueError("COURSEFUZZ_GITHUB_INSTALLATIONS_JSON must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("COURSEFUZZ_GITHUB_INSTALLATIONS_JSON must be an object")
        return cls(
            app_id=app_id,
            private_key_pem=private_key,
            installations=parsed,
            client=client,
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def repositories(self) -> frozenset[str]:
        return frozenset(
            repository
            for repository_map in self._installations.values()
            for repository in repository_map
        )

    @property
    def mode(self) -> str:
        return "github-app"

    def allows(self, repository: str, tenant_id: str = LOCAL_TENANT) -> bool:
        return repository.lower() in self._installations.get(tenant_id, {})

    def token_for(self, repository: str, tenant_id: str = LOCAL_TENANT) -> str:
        with self._cache_lock:
            return self._token_for_locked(repository, tenant_id)

    def _token_for_locked(self, repository: str, tenant_id: str) -> str:
        normalized = repository.lower()
        installation_id = self._installations.get(tenant_id, {}).get(normalized)
        if installation_id is None:
            raise RuntimeError(
                f"GitHub repository {repository!r} has no App installation for this workspace"
            )
        cache_key = (installation_id, normalized)
        now = self._utc_now()
        cached = self._cache.get(cache_key)
        if cached and now + timedelta(seconds=60) < cached.expires_at:
            return cached.token

        repository_name = normalized.split("/", 1)[1]
        response = self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._app_jwt(now)}",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            json={
                "repositories": [repository_name],
                "permissions": {
                    "checks": "read",
                    "contents": "write",
                    "pull_requests": "write",
                },
            },
        )
        if response.status_code != 201:
            raise RuntimeError(
                "GitHub App installation token request failed with status "
                f"{response.status_code}: {response.text[:300]}"
            )
        body = response.json()
        token = body.get("token")
        expires_at = body.get("expires_at")
        if not isinstance(token, str) or not token or not isinstance(expires_at, str):
            raise RuntimeError("GitHub App token response omitted token or expiry")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as exc:
            raise RuntimeError("GitHub App token response returned an invalid expiry") from exc
        self._cache[cache_key] = _CachedInstallationToken(token, expiry)
        return token

    def _app_jwt(self, now: datetime) -> str:
        issued_at = int(now.timestamp()) - 60
        payload = {
            "iat": issued_at,
            "exp": issued_at + 9 * 60,
            "iss": self._app_id,
        }
        header_segment = self._segment({"alg": "RS256", "typ": "JWT"})
        payload_segment = self._segment(payload)
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = self._private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header_segment}.{payload_segment}.{self._b64url(signature)}"

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("GitHub App clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @classmethod
    def _segment(cls, value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return cls._b64url(raw)

    @staticmethod
    def _b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def credential_provider_from_env(
    *,
    client: httpx.Client | None = None,
) -> GitHubCredentialProvider:
    app_provider = GitHubAppCredentialProvider.from_env(client=client)
    if app_provider is not None:
        return app_provider
    return StaticGitHubCredentialProvider(os.getenv("COURSEFUZZ_GITHUB_TOKEN"))
