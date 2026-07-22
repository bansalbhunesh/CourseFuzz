from __future__ import annotations

import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Final

LOCAL_TENANT: Final = "local-demo"
GLOBAL_TENANT: Final = "*"
SESSION_COOKIE: Final = "coursefuzz_session"
JUDGE_TENANT: Final = "judge-review"
_TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str


class AccessPolicy:
    """Resolve opaque credentials to stable tenant identities.

    An empty key map is an explicit local-demo mode. A configured instance is
    fail-closed: every protected request must supply a matching bearer token or
    HttpOnly session cookie.
    """

    def __init__(self, tenant_tokens: dict[str, str] | None = None) -> None:
        self._tenant_tokens = tenant_tokens or {}
        for tenant_id, token in self._tenant_tokens.items():
            if not _TENANT_PATTERN.fullmatch(tenant_id):
                raise ValueError(f"Invalid CourseFuzz tenant ID: {tenant_id!r}")
            if len(token) < 24:
                raise ValueError(
                    f"Access token for {tenant_id!r} must contain at least 24 characters"
                )
        if len(set(self._tenant_tokens.values())) != len(self._tenant_tokens):
            raise ValueError("Each CourseFuzz tenant must use a distinct access token")

    @classmethod
    def from_env(cls) -> AccessPolicy:
        raw = os.getenv("COURSEFUZZ_ACCESS_KEYS_JSON", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("COURSEFUZZ_ACCESS_KEYS_JSON must be valid JSON") from exc
            if not isinstance(parsed, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
            ):
                raise ValueError("COURSEFUZZ_ACCESS_KEYS_JSON must map tenant IDs to opaque tokens")
            tenant_tokens = dict(parsed)
        else:
            tenant_tokens = {}

        judge_token = os.getenv("COURSEFUZZ_JUDGE_ACCESS_TOKEN", "").strip()
        if judge_token:
            existing = tenant_tokens.get(JUDGE_TENANT)
            if existing is not None and not hmac.compare_digest(existing, judge_token):
                raise ValueError(
                    f"{JUDGE_TENANT!r} is configured differently in the access-key map"
                )
            tenant_tokens[JUDGE_TENANT] = judge_token
        return cls(tenant_tokens)

    @property
    def required(self) -> bool:
        return bool(self._tenant_tokens)

    @property
    def mode(self) -> str:
        return "required" if self.required else "local-demo"

    def authenticate(
        self,
        authorization: str | None = None,
        session_cookie: str | None = None,
    ) -> Principal:
        if not self.required:
            return Principal(LOCAL_TENANT)
        credential = self._bearer_token(authorization) or session_cookie
        if not credential:
            raise PermissionError("Authentication required")
        for tenant_id, expected in self._tenant_tokens.items():
            if hmac.compare_digest(credential, expected):
                return Principal(tenant_id)
        raise PermissionError("Invalid CourseFuzz credential")

    @staticmethod
    def _bearer_token(authorization: str | None) -> str | None:
        if not authorization:
            return None
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and token:
            return token
        return None
