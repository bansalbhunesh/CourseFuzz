from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Final

# GitHub App webhook headers.
SIGNATURE_HEADER: Final = "X-Hub-Signature-256"
DELIVERY_HEADER: Final = "X-GitHub-Delivery"
EVENT_HEADER: Final = "X-GitHub-Event"

# Installation lifecycle actions we act on. Everything else is acknowledged but ignored.
_FULL_SYNC_ACTIONS: Final = frozenset({"created", "unsuspend"})
_ADD_ACTIONS: Final = frozenset({"added"})
_REMOVE_ACTIONS: Final = frozenset({"removed"})
_DELETE_ACTIONS: Final = frozenset({"deleted"})
_SUSPEND_ACTIONS: Final = frozenset({"suspend"})
_HANDLED_EVENTS: Final = frozenset({"installation", "installation_repositories"})

_REPOSITORY_PATTERN: Final = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time verification of a GitHub ``X-Hub-Signature-256`` header.

    Fail-closed: an empty secret, a missing/malformed header, or any mismatch returns ``False``.
    GitHub signs the raw request body with HMAC-SHA256 keyed by the App's webhook secret.
    """

    if not secret or not signature_header:
        return False
    scheme, separator, provided = signature_header.partition("=")
    if not separator or scheme != "sha256" or not provided:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided.strip())


@dataclass(frozen=True, slots=True)
class InstallationEvent:
    """A normalized GitHub App installation intent the store can apply idempotently.

    ``action`` is one of created/unsuspend (full repository sync), added, removed, deleted, or
    suspend. ``repositories`` holds the relevant lowercase ``owner/name`` set for the action.
    """

    installation_id: int
    account_login: str | None
    action: str
    repositories: frozenset[str]

    @property
    def is_full_sync(self) -> bool:
        return self.action in _FULL_SYNC_ACTIONS

    @property
    def is_add(self) -> bool:
        return self.action in _ADD_ACTIONS

    @property
    def is_remove(self) -> bool:
        return self.action in _REMOVE_ACTIONS

    @property
    def is_delete(self) -> bool:
        return self.action in _DELETE_ACTIONS

    @property
    def is_suspend(self) -> bool:
        return self.action in _SUSPEND_ACTIONS


def _clean_repositories(entries: object) -> frozenset[str]:
    if not isinstance(entries, list):
        return frozenset()
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        full_name = entry.get("full_name")
        if isinstance(full_name, str) and _REPOSITORY_PATTERN.fullmatch(full_name.lower()):
            names.add(full_name.lower())
    return frozenset(names)


def parse_installation_event(event_type: str, payload: dict) -> InstallationEvent | None:
    """Turn a verified webhook body into an :class:`InstallationEvent`, or ``None`` to ignore.

    Only ``installation`` and ``installation_repositories`` events with a valid installation ID and
    a recognized action are actionable; any other event, action, or malformed body is ignored so an
    unexpected delivery can never mutate the credential store.
    """

    if event_type not in _HANDLED_EVENTS or not isinstance(payload, dict):
        return None
    installation = payload.get("installation")
    if not isinstance(installation, dict):
        return None
    installation_id = installation.get("id")
    if not isinstance(installation_id, int) or isinstance(installation_id, bool):
        return None
    if installation_id <= 0:
        return None
    account = installation.get("account")
    account_login = account.get("login") if isinstance(account, dict) else None
    if account_login is not None and not isinstance(account_login, str):
        account_login = None

    action = payload.get("action")
    if not isinstance(action, str):
        return None

    if event_type == "installation":
        if action in _FULL_SYNC_ACTIONS:
            repositories = _clean_repositories(payload.get("repositories"))
        elif action in _DELETE_ACTIONS or action in _SUSPEND_ACTIONS:
            repositories = frozenset()
        else:
            return None
    else:  # installation_repositories
        if action in _ADD_ACTIONS:
            repositories = _clean_repositories(payload.get("repositories_added"))
        elif action in _REMOVE_ACTIONS:
            repositories = _clean_repositories(payload.get("repositories_removed"))
        else:
            return None

    return InstallationEvent(
        installation_id=installation_id,
        account_login=account_login,
        action=action,
        repositories=repositories,
    )
