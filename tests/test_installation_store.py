from pathlib import Path

from coursefuzz.security.installations import (
    SqliteInstallationStore,
    apply_installation_event,
)
from coursefuzz.security.webhooks import parse_installation_event


def _store(tmp_path: Path) -> SqliteInstallationStore:
    return SqliteInstallationStore(tmp_path / "installs.db")


def _event(event_type: str, payload: dict):
    parsed = parse_installation_event(event_type, payload)
    assert parsed is not None
    return parsed


def test_delivery_dedup_applies_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.record_delivery("delivery-1") is True
    assert store.record_delivery("delivery-1") is False
    assert store.record_delivery("delivery-2") is True


def test_created_then_added_then_removed_lifecycle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "created",
                "installation": {"id": 100, "account": {"login": "acme"}},
                "repositories": [{"full_name": "acme/course-a"}],
            },
        ),
    )
    assert store.repositories_for_installation(100) == frozenset({"acme/course-a"})

    apply_installation_event(
        store,
        _event(
            "installation_repositories",
            {
                "action": "added",
                "installation": {"id": 100, "account": {"login": "acme"}},
                "repositories_added": [{"full_name": "acme/course-b"}],
            },
        ),
    )
    assert store.repositories_for_installation(100) == frozenset({"acme/course-a", "acme/course-b"})

    apply_installation_event(
        store,
        _event(
            "installation_repositories",
            {
                "action": "removed",
                "installation": {"id": 100, "account": {"login": "acme"}},
                "repositories_removed": [{"full_name": "acme/course-a"}],
            },
        ),
    )
    assert store.repositories_for_installation(100) == frozenset({"acme/course-b"})


def test_suspend_hides_repositories_and_unsuspend_restores(tmp_path: Path) -> None:
    store = _store(tmp_path)
    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "created",
                "installation": {"id": 200, "account": {"login": "acme"}},
                "repositories": [{"full_name": "acme/course"}],
            },
        ),
    )
    apply_installation_event(
        store, _event("installation", {"action": "suspend", "installation": {"id": 200}})
    )
    assert store.installation_exists(200) is False
    assert store.repositories_for_installation(200) == frozenset()

    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "unsuspend",
                "installation": {"id": 200, "account": {"login": "acme"}},
                "repositories": [{"full_name": "acme/course"}],
            },
        ),
    )
    assert store.installation_exists(200) is True
    assert store.repositories_for_installation(200) == frozenset({"acme/course"})


def test_delete_removes_installation_and_bindings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "created",
                "installation": {"id": 300, "account": {"login": "acme"}},
                "repositories": [{"full_name": "acme/course"}],
            },
        ),
    )
    store.bind_workspace("course-team", 300)
    assert store.installation_for_workspace("course-team") == 300

    apply_installation_event(
        store, _event("installation", {"action": "deleted", "installation": {"id": 300}})
    )
    assert store.installation_exists(300) is False
    assert store.installation_for_workspace("course-team") is None


def test_workspace_binding_scopes_repository_listing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "created",
                "installation": {"id": 400, "account": {"login": "acme"}},
                "repositories": [
                    {"full_name": "acme/course-a"},
                    {"full_name": "acme/course-b"},
                ],
            },
        ),
    )
    # No binding yet -> nothing visible to the workspace.
    assert store.repositories_for_workspace("team-x") == []
    store.bind_workspace("team-x", 400)
    assert store.repositories_for_workspace("team-x") == ["acme/course-a", "acme/course-b"]
    # A different, unbound workspace still sees nothing (tenant scoping).
    assert store.repositories_for_workspace("team-y") == []


def test_binding_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "installs.db"
    store = SqliteInstallationStore(path)
    apply_installation_event(
        store,
        _event(
            "installation",
            {
                "action": "created",
                "installation": {"id": 500, "account": {"login": "acme"}},
                "repositories": [{"full_name": "acme/course"}],
            },
        ),
    )
    store.bind_workspace("team", 500)
    del store

    reopened = SqliteInstallationStore(path)
    assert reopened.installation_for_workspace("team") == 500
    assert reopened.repositories_for_workspace("team") == ["acme/course"]
