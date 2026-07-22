from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from coursefuzz.security.webhooks import InstallationEvent


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class InstallationStore(Protocol):
    """Durable record of GitHub App installations learned from signed webhooks.

    Persists which repositories each installation covers, which workspace (CourseFuzz tenant) has
    claimed an installation, and which webhook deliveries have already been applied. This is what
    lets instructors self-onboard a repository instead of an operator editing an installations map.
    """

    backend_name: str

    def record_delivery(self, delivery_id: str) -> bool: ...

    def upsert_installation(
        self,
        installation_id: int,
        account_login: str | None,
        suspended: bool | None = None,
    ) -> None: ...

    def set_repositories(self, installation_id: int, repositories: Iterable[str]) -> None: ...

    def add_repositories(self, installation_id: int, repositories: Iterable[str]) -> None: ...

    def remove_repositories(self, installation_id: int, repositories: Iterable[str]) -> None: ...

    def delete_installation(self, installation_id: int) -> None: ...

    def installation_exists(self, installation_id: int) -> bool: ...

    def repositories_for_installation(self, installation_id: int) -> frozenset[str]: ...

    def bind_workspace(self, tenant_id: str, installation_id: int) -> None: ...

    def claim_installation(self, tenant_id: str, installation_id: int) -> bool: ...

    def installation_for_workspace(self, tenant_id: str) -> int | None: ...

    def repositories_for_workspace(self, tenant_id: str) -> list[str]: ...


def apply_installation_event(store: InstallationStore, event: InstallationEvent) -> None:
    """Apply one normalized installation intent to the store, idempotently.

    A ``deleted`` event removes the installation entirely; ``suspend`` marks it inactive;
    ``created``/``unsuspend`` reactivate it and replace its repository set; ``added``/``removed``
    adjust the set incrementally. Suspension is only touched by lifecycle actions, never by a
    repository add/remove.
    """

    if event.is_delete:
        store.delete_installation(event.installation_id)
        return
    if event.is_suspend:
        store.upsert_installation(event.installation_id, event.account_login, suspended=True)
        return
    store.upsert_installation(
        event.installation_id,
        event.account_login,
        suspended=False if event.is_full_sync else None,
    )
    if event.is_full_sync:
        store.set_repositories(event.installation_id, event.repositories)
    elif event.is_add:
        store.add_repositories(event.installation_id, event.repositories)
    elif event.is_remove:
        store.remove_repositories(event.installation_id, event.repositories)


class SqliteInstallationStore:
    backend_name = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS github_installations (
                    installation_id INTEGER PRIMARY KEY,
                    account_login TEXT,
                    suspended INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS github_installation_repos (
                    installation_id INTEGER NOT NULL
                        REFERENCES github_installations(installation_id) ON DELETE CASCADE,
                    repository TEXT NOT NULL,
                    PRIMARY KEY (installation_id, repository)
                );
                CREATE TABLE IF NOT EXISTS github_workspace_installations (
                    tenant_id TEXT PRIMARY KEY,
                    installation_id INTEGER NOT NULL,
                    bound_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL
                );
                """
            )

    def record_delivery(self, delivery_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO github_webhook_deliveries(delivery_id, received_at) "
                "VALUES (?, ?)",
                (delivery_id, _utc_iso()),
            )
            return cursor.rowcount == 1

    def upsert_installation(
        self,
        installation_id: int,
        account_login: str | None,
        suspended: bool | None = None,
    ) -> None:
        with self._connect() as connection:
            if suspended is None:
                connection.execute(
                    "INSERT INTO github_installations"
                    "(installation_id, account_login, suspended, updated_at) "
                    "VALUES (?, ?, 0, ?) "
                    "ON CONFLICT(installation_id) DO UPDATE SET "
                    "account_login=COALESCE(excluded.account_login, "
                    "github_installations.account_login), updated_at=excluded.updated_at",
                    (installation_id, account_login, _utc_iso()),
                )
            else:
                connection.execute(
                    "INSERT INTO github_installations"
                    "(installation_id, account_login, suspended, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(installation_id) DO UPDATE SET "
                    "account_login=COALESCE(excluded.account_login, "
                    "github_installations.account_login), suspended=excluded.suspended, "
                    "updated_at=excluded.updated_at",
                    (installation_id, account_login, 1 if suspended else 0, _utc_iso()),
                )

    def set_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM github_installation_repos WHERE installation_id = ?",
                (installation_id,),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO github_installation_repos"
                "(installation_id, repository) VALUES (?, ?)",
                [(installation_id, repo.lower()) for repo in repositories],
            )

    def add_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO github_installation_repos"
                "(installation_id, repository) VALUES (?, ?)",
                [(installation_id, repo.lower()) for repo in repositories],
            )

    def remove_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM github_installation_repos "
                "WHERE installation_id = ? AND repository = ?",
                [(installation_id, repo.lower()) for repo in repositories],
            )

    def delete_installation(self, installation_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM github_installation_repos WHERE installation_id = ?",
                (installation_id,),
            )
            connection.execute(
                "DELETE FROM github_workspace_installations WHERE installation_id = ?",
                (installation_id,),
            )
            connection.execute(
                "DELETE FROM github_installations WHERE installation_id = ?",
                (installation_id,),
            )

    def installation_exists(self, installation_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM github_installations WHERE installation_id = ? AND suspended = 0",
                (installation_id,),
            ).fetchone()
        return row is not None

    def repositories_for_installation(self, installation_id: int) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT repository FROM github_installation_repos "
                "WHERE installation_id = ? AND installation_id IN "
                "(SELECT installation_id FROM github_installations WHERE suspended = 0)",
                (installation_id,),
            ).fetchall()
        return frozenset(row["repository"] for row in rows)

    def bind_workspace(self, tenant_id: str, installation_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO github_workspace_installations"
                "(tenant_id, installation_id, bound_at) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "installation_id=excluded.installation_id, bound_at=excluded.bound_at",
                (tenant_id, installation_id, _utc_iso()),
            )

    def claim_installation(self, tenant_id: str, installation_id: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM github_installations WHERE installation_id = ? AND suspended = 0",
                (installation_id,),
            ).fetchone()
            if not active:
                return False
            owner = connection.execute(
                "SELECT tenant_id FROM github_workspace_installations WHERE installation_id = ?",
                (installation_id,),
            ).fetchone()
            if owner and owner["tenant_id"] != tenant_id:
                return False
            connection.execute(
                "INSERT INTO github_workspace_installations"
                "(tenant_id, installation_id, bound_at) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET "
                "installation_id=excluded.installation_id, bound_at=excluded.bound_at",
                (tenant_id, installation_id, _utc_iso()),
            )
            return True

    def installation_for_workspace(self, tenant_id: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT installation_id FROM github_workspace_installations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return int(row["installation_id"]) if row else None

    def repositories_for_workspace(self, tenant_id: str) -> list[str]:
        installation_id = self.installation_for_workspace(tenant_id)
        if installation_id is None:
            return []
        return sorted(self.repositories_for_installation(installation_id))


class PostgresInstallationStore:
    backend_name = "postgres"

    def __init__(self, dsn: str) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn
        self._initialize()

    def _connect(self):  # type: ignore[no-untyped-def]
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row, connect_timeout=10)

    def _initialize(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS github_installations (
                installation_id BIGINT PRIMARY KEY,
                account_login TEXT,
                suspended BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS github_installation_repos (
                installation_id BIGINT NOT NULL
                    REFERENCES github_installations(installation_id) ON DELETE CASCADE,
                repository TEXT NOT NULL,
                PRIMARY KEY (installation_id, repository)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS github_workspace_installations (
                tenant_id TEXT PRIMARY KEY,
                installation_id BIGINT NOT NULL,
                bound_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
                delivery_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL
            )
            """,
        )
        with self._connect() as connection:
            for statement in statements:
                connection.execute(statement)

    def record_delivery(self, delivery_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO github_webhook_deliveries(delivery_id, received_at) "
                "VALUES (%s, %s) ON CONFLICT (delivery_id) DO NOTHING",
                (delivery_id, _utc_iso()),
            )
            return cursor.rowcount == 1

    def upsert_installation(
        self,
        installation_id: int,
        account_login: str | None,
        suspended: bool | None = None,
    ) -> None:
        with self._connect() as connection:
            if suspended is None:
                connection.execute(
                    "INSERT INTO github_installations"
                    "(installation_id, account_login, suspended, updated_at) "
                    "VALUES (%s, %s, FALSE, %s) "
                    "ON CONFLICT (installation_id) DO UPDATE SET "
                    "account_login=COALESCE(EXCLUDED.account_login, "
                    "github_installations.account_login), updated_at=EXCLUDED.updated_at",
                    (installation_id, account_login, _utc_iso()),
                )
            else:
                connection.execute(
                    "INSERT INTO github_installations"
                    "(installation_id, account_login, suspended, updated_at) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (installation_id) DO UPDATE SET "
                    "account_login=COALESCE(EXCLUDED.account_login, "
                    "github_installations.account_login), suspended=EXCLUDED.suspended, "
                    "updated_at=EXCLUDED.updated_at",
                    (installation_id, account_login, suspended, _utc_iso()),
                )

    def set_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM github_installation_repos WHERE installation_id = %s",
                (installation_id,),
            )
            for repo in repositories:
                connection.execute(
                    "INSERT INTO github_installation_repos(installation_id, repository) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (installation_id, repo.lower()),
                )

    def add_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            for repo in repositories:
                connection.execute(
                    "INSERT INTO github_installation_repos(installation_id, repository) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (installation_id, repo.lower()),
                )

    def remove_repositories(self, installation_id: int, repositories: Iterable[str]) -> None:
        with self._connect() as connection:
            for repo in repositories:
                connection.execute(
                    "DELETE FROM github_installation_repos "
                    "WHERE installation_id = %s AND repository = %s",
                    (installation_id, repo.lower()),
                )

    def delete_installation(self, installation_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM github_workspace_installations WHERE installation_id = %s",
                (installation_id,),
            )
            connection.execute(
                "DELETE FROM github_installations WHERE installation_id = %s",
                (installation_id,),
            )

    def installation_exists(self, installation_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM github_installations "
                "WHERE installation_id = %s AND suspended = FALSE",
                (installation_id,),
            ).fetchone()
        return row is not None

    def repositories_for_installation(self, installation_id: int) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT repository FROM github_installation_repos "
                "WHERE installation_id = %s AND installation_id IN "
                "(SELECT installation_id FROM github_installations WHERE suspended = FALSE)",
                (installation_id,),
            ).fetchall()
        return frozenset(row["repository"] for row in rows)

    def bind_workspace(self, tenant_id: str, installation_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO github_workspace_installations"
                "(tenant_id, installation_id, bound_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "installation_id=EXCLUDED.installation_id, bound_at=EXCLUDED.bound_at",
                (tenant_id, installation_id, _utc_iso()),
            )

    def claim_installation(self, tenant_id: str, installation_id: int) -> bool:
        with self._connect() as connection:
            active = connection.execute(
                "SELECT 1 FROM github_installations "
                "WHERE installation_id = %s AND suspended = FALSE FOR UPDATE",
                (installation_id,),
            ).fetchone()
            if not active:
                return False
            owner = connection.execute(
                "SELECT tenant_id FROM github_workspace_installations "
                "WHERE installation_id = %s FOR UPDATE",
                (installation_id,),
            ).fetchone()
            if owner and owner["tenant_id"] != tenant_id:
                return False
            connection.execute(
                "INSERT INTO github_workspace_installations"
                "(tenant_id, installation_id, bound_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "installation_id=EXCLUDED.installation_id, bound_at=EXCLUDED.bound_at",
                (tenant_id, installation_id, _utc_iso()),
            )
            return True

    def installation_for_workspace(self, tenant_id: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT installation_id FROM github_workspace_installations WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
        return int(row["installation_id"]) if row else None

    def repositories_for_workspace(self, tenant_id: str) -> list[str]:
        installation_id = self.installation_for_workspace(tenant_id)
        if installation_id is None:
            return []
        return sorted(self.repositories_for_installation(installation_id))


def build_installation_store(
    database_url: str | None,
    database_path: str | Path,
) -> InstallationStore:
    """Pick the durable store backend that matches the run repository."""

    if database_url:
        return PostgresInstallationStore(database_url)
    return SqliteInstallationStore(database_path)
