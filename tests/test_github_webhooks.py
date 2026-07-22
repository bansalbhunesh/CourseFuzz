import hashlib
import hmac

from coursefuzz.security.webhooks import (
    parse_installation_event,
    verify_github_signature,
)

SECRET = "s3cr3t-webhook-signing-key"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_verifies() -> None:
    body = b'{"action":"created"}'
    assert verify_github_signature(SECRET, body, _sign(body)) is True


def test_tampered_body_fails() -> None:
    body = b'{"action":"created"}'
    signature = _sign(body)
    assert verify_github_signature(SECRET, body + b" ", signature) is False


def test_wrong_secret_fails() -> None:
    body = b'{"action":"created"}'
    assert verify_github_signature("other-secret", body, _sign(body)) is False


def test_missing_or_malformed_signature_fails_closed() -> None:
    body = b"{}"
    assert verify_github_signature(SECRET, body, None) is False
    assert verify_github_signature(SECRET, body, "") is False
    assert verify_github_signature(SECRET, body, "deadbeef") is False
    assert verify_github_signature(SECRET, body, "sha1=abc") is False
    assert verify_github_signature("", body, _sign(body)) is False


def test_parse_installation_created_is_full_sync() -> None:
    event = parse_installation_event(
        "installation",
        {
            "action": "created",
            "installation": {"id": 42, "account": {"login": "acme-university"}},
            "repositories": [
                {"full_name": "acme-university/CS101-autograder"},
                {"full_name": "acme-university/CS102-autograder"},
            ],
        },
    )
    assert event is not None
    assert event.installation_id == 42
    assert event.account_login == "acme-university"
    assert event.is_full_sync is True
    assert event.repositories == frozenset(
        {"acme-university/cs101-autograder", "acme-university/cs102-autograder"}
    )


def test_parse_repositories_added_and_removed() -> None:
    added = parse_installation_event(
        "installation_repositories",
        {
            "action": "added",
            "installation": {"id": 7, "account": {"login": "acme"}},
            "repositories_added": [{"full_name": "acme/new-course"}],
        },
    )
    assert added is not None and added.is_add
    assert added.repositories == frozenset({"acme/new-course"})

    removed = parse_installation_event(
        "installation_repositories",
        {
            "action": "removed",
            "installation": {"id": 7, "account": {"login": "acme"}},
            "repositories_removed": [{"full_name": "acme/old-course"}],
        },
    )
    assert removed is not None and removed.is_remove
    assert removed.repositories == frozenset({"acme/old-course"})


def test_parse_delete_and_suspend() -> None:
    deleted = parse_installation_event(
        "installation", {"action": "deleted", "installation": {"id": 9}}
    )
    assert deleted is not None and deleted.is_delete and deleted.repositories == frozenset()

    suspended = parse_installation_event(
        "installation", {"action": "suspend", "installation": {"id": 9}}
    )
    assert suspended is not None and suspended.is_suspend


def test_unknown_or_malformed_events_are_ignored() -> None:
    assert parse_installation_event("push", {"installation": {"id": 1}}) is None
    assert (
        parse_installation_event("installation", {"action": "edited", "installation": {"id": 1}})
        is None
    )
    assert parse_installation_event("installation", {"action": "created"}) is None
    assert (
        parse_installation_event("installation", {"action": "created", "installation": {}}) is None
    )
    assert (
        parse_installation_event("installation", {"action": "created", "installation": {"id": -3}})
        is None
    )
    assert (
        parse_installation_event(
            "installation", {"action": "created", "installation": {"id": True}}
        )
        is None
    )


def test_malformed_repository_names_are_dropped() -> None:
    event = parse_installation_event(
        "installation",
        {
            "action": "created",
            "installation": {"id": 5, "account": {"login": "a"}},
            "repositories": [
                {"full_name": "a/good-repo"},
                {"full_name": "not-a-full-name"},
                {"full_name": "a/bad name"},
                {"nope": "x"},
                "junk",
            ],
        },
    )
    assert event is not None
    assert event.repositories == frozenset({"a/good-repo"})
