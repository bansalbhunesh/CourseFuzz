from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

_AUTHORIZE_PATH = "/login/oauth/authorize"
_TOKEN_PATH = "/login/oauth/access_token"
# A user-to-server token can list the App installations the user can administer.
_USER_INSTALLATIONS_PATH = "/user/installations"
_STATE_MAX_AGE_SECONDS = 600  # A GitHub OAuth round trip should complete well within ten minutes.


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def sign_state(payload: dict, secret: str) -> str:
    """Sign an OAuth ``state`` value so the callback can trust the initiating tenant/installation.

    The state is not encrypted (it carries no secret), only authenticated: an HMAC over a compact
    JSON body keyed by the OAuth client secret, which never leaves the server.
    """

    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64url(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def verify_state(
    token: str,
    secret: str,
    *,
    max_age_seconds: int = _STATE_MAX_AGE_SECONDS,
    now: float | None = None,
) -> dict | None:
    """Verify a signed state token; return its payload, or ``None`` if invalid/forged/expired."""

    if not token or not secret:
        return None
    body, separator, signature = token.partition(".")
    if not separator or not body or not signature:
        return None
    expected = _b64url(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    issued_at = payload.get("iat")
    if not isinstance(issued_at, (int, float)):
        return None
    current = time.time() if now is None else now
    if current - issued_at > max_age_seconds or issued_at - current > 60:
        return None
    return payload


@dataclass(slots=True)
class GitHubOAuthClient:
    """GitHub App user-to-server OAuth: authorize URL, code exchange, and installation ownership.

    The two GitHub hosts are separate httpx clients so tests can mock them independently: OAuth
    authorize/token live on ``github.com`` while installation listing is on ``api.github.com``.
    """

    client_id: str
    client_secret: str
    oauth_client: httpx.Client
    api_client: httpx.Client

    @classmethod
    def from_env(
        cls,
        *,
        oauth_client: httpx.Client | None = None,
        api_client: httpx.Client | None = None,
    ) -> GitHubOAuthClient | None:
        client_id = os.getenv("COURSEFUZZ_GITHUB_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("COURSEFUZZ_GITHUB_OAUTH_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            oauth_client=oauth_client or httpx.Client(base_url="https://github.com", timeout=10.0),
            api_client=api_client or httpx.Client(base_url="https://api.github.com", timeout=10.0),
        )

    @property
    def state_secret(self) -> str:
        # The client secret is server-only and already required; reuse it to key state HMACs.
        return self.client_secret

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"https://github.com{_AUTHORIZE_PATH}?{query}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> str:
        response = self.oauth_client.post(
            _TOKEN_PATH,
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"GitHub OAuth token exchange failed: {response.status_code}")
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(
                f"GitHub OAuth token exchange returned no token: {body.get('error')}"
            )
        return token

    def user_installation_ids(self, user_token: str) -> frozenset[int]:
        ids: set[int] = set()
        for page in range(1, 11):  # bounded pagination; 1000 installations is far beyond any user
            response = self.api_client.get(
                _USER_INSTALLATIONS_PATH,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {user_token}",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
                params={"per_page": 100, "page": page},
            )
            if response.status_code != 200:
                raise RuntimeError(f"GitHub /user/installations failed: {response.status_code}")
            installations = response.json().get("installations") or []
            for installation in installations:
                identifier = installation.get("id")
                if isinstance(identifier, int) and not isinstance(identifier, bool):
                    ids.add(identifier)
            if len(installations) < 100:
                break
        return frozenset(ids)
