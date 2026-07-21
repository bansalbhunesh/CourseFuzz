from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from coursefuzz.domain.models import AssignmentSnapshot, AuditEvent, RunStatus, RunView
from coursefuzz.repositories.types import ArtifactRecord
from coursefuzz.security.access import GLOBAL_TENANT, LOCAL_TENANT


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class RunRepository:
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
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    document TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT 'local-demo'
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_id_id ON events(run_id, id);
                CREATE TABLE IF NOT EXISTS approvals (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    payload_sha256 TEXT NOT NULL,
                    approval_token TEXT NOT NULL UNIQUE,
                    approved_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    filename TEXT,
                    content BLOB,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    snapshot_sha256 TEXT NOT NULL UNIQUE,
                    document TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assignments_created_at
                    ON assignments(created_at DESC);
                CREATE TABLE IF NOT EXISTS assignment_access (
                    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL,
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY (assignment_id, tenant_id)
                );
                CREATE INDEX IF NOT EXISTS assignment_access_tenant
                    ON assignment_access(tenant_id, assignment_id);
                """
            )
            run_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "owner_id" not in run_columns:
                connection.execute(
                    "ALTER TABLE runs ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'local-demo'"
                )
            artifact_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
            }
            if "filename" not in artifact_columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN filename TEXT")
            if "content" not in artifact_columns:
                connection.execute("ALTER TABLE artifacts ADD COLUMN content BLOB")
            connection.execute(
                "UPDATE runs SET idempotency_key = owner_id || ':' || idempotency_key "
                "WHERE substr(idempotency_key, 1, length(owner_id) + 1) != owner_id || ':'"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS runs_owner_updated "
                "ON runs(owner_id, updated_at DESC)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO assignment_access(assignment_id, tenant_id, granted_at) "
                "SELECT assignments.id, ?, assignments.created_at FROM assignments "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM assignment_access "
                "WHERE assignment_access.assignment_id = assignments.id)",
                (LOCAL_TENANT,),
            )

    def create_assignment(
        self,
        snapshot: AssignmentSnapshot,
        tenant_id: str = LOCAL_TENANT,
    ) -> tuple[AssignmentSnapshot, bool]:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT document FROM assignments WHERE snapshot_sha256 = ?",
                (snapshot.snapshot_sha256,),
            ).fetchone()
            if existing:
                stored = AssignmentSnapshot.model_validate_json(existing["document"])
                connection.execute(
                    "INSERT OR IGNORE INTO assignment_access"
                    "(assignment_id, tenant_id, granted_at) VALUES (?, ?, ?)",
                    (stored.id, tenant_id, _utc_iso()),
                )
                return stored, False
            existing_id = connection.execute(
                "SELECT document FROM assignments WHERE id = ?",
                (snapshot.id,),
            ).fetchone()
            if existing_id:
                stored = AssignmentSnapshot.model_validate_json(existing_id["document"])
                if stored.provenance != "seeded" or snapshot.provenance != "seeded":
                    raise ValueError("Assignment ID collision for an immutable manual snapshot")
                connection.execute(
                    "UPDATE assignments SET snapshot_sha256 = ?, document = ?, created_at = ? "
                    "WHERE id = ?",
                    (
                        snapshot.snapshot_sha256,
                        snapshot.model_dump_json(),
                        snapshot.created_at.isoformat(),
                        snapshot.id,
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO assignment_access"
                    "(assignment_id, tenant_id, granted_at) VALUES (?, ?, ?)",
                    (snapshot.id, tenant_id, _utc_iso()),
                )
                return snapshot, False
            connection.execute(
                "INSERT INTO assignments(id, snapshot_sha256, document, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    snapshot.id,
                    snapshot.snapshot_sha256,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO assignment_access(assignment_id, tenant_id, granted_at) "
                "VALUES (?, ?, ?)",
                (snapshot.id, tenant_id, _utc_iso()),
            )
        return snapshot, True

    def get_assignment(
        self,
        assignment_id: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> AssignmentSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT assignments.document FROM assignments "
                "JOIN assignment_access ON assignment_access.assignment_id = assignments.id "
                "WHERE assignments.id = ? AND assignment_access.tenant_id IN (?, ?) "
                "ORDER BY assignment_access.tenant_id = ? DESC LIMIT 1",
                (assignment_id, tenant_id, GLOBAL_TENANT, tenant_id),
            ).fetchone()
        return AssignmentSnapshot.model_validate_json(row["document"]) if row else None

    def list_assignments(self, tenant_id: str = LOCAL_TENANT) -> list[AssignmentSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT assignments.document, assignments.created_at "
                "FROM assignments "
                "JOIN assignment_access ON assignment_access.assignment_id = assignments.id "
                "WHERE assignment_access.tenant_id IN (?, ?) "
                "ORDER BY assignments.created_at DESC",
                (tenant_id, GLOBAL_TENANT),
            ).fetchall()
        return [AssignmentSnapshot.model_validate_json(row["document"]) for row in rows]

    def create(
        self,
        run: RunView,
        idempotency_key: str,
        owner_id: str = LOCAL_TENANT,
    ) -> tuple[RunView, bool]:
        scoped_idempotency_key = f"{owner_id}:{idempotency_key}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT document FROM runs WHERE idempotency_key = ?",
                (scoped_idempotency_key,),
            ).fetchone()
            if existing:
                return RunView.model_validate_json(existing["document"]), False
            now = _utc_iso()
            connection.execute(
                "INSERT INTO runs"
                "(id, idempotency_key, document, created_at, updated_at, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run.id, scoped_idempotency_key, run.model_dump_json(), now, now, owner_id),
            )
        return run, True

    def get(self, run_id: str, owner_id: str = LOCAL_TENANT) -> RunView | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM runs WHERE id = ? AND owner_id = ?",
                (run_id, owner_id),
            ).fetchone()
        return RunView.model_validate_json(row["document"]) if row else None

    def list_runs(
        self,
        assignment_id: str | None = None,
        owner_id: str = LOCAL_TENANT,
    ) -> list[RunView]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document FROM runs WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        runs = [RunView.model_validate_json(row["document"]) for row in rows]
        if assignment_id is not None:
            runs = [run for run in runs if run.assignment_id == assignment_id]
        return runs

    def list_recoverable_runs(self, limit: int = 10) -> list[tuple[str, RunView]]:
        statuses = ("queued", "analyzing", "applying", "external_ci_pending")
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT owner_id, document FROM runs WHERE json_extract(document, '$.status') "
                f"IN ({placeholders}) ORDER BY updated_at ASC LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        return [
            (row["owner_id"], RunView.model_validate_json(row["document"])) for row in rows
        ]

    def save(self, run: RunView) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET document = ?, updated_at = ? WHERE id = ?",
                (run.model_dump_json(), _utc_iso(), run.id),
            )
            if cursor.rowcount != 1:
                raise KeyError(run.id)

    def claim_approved_apply(
        self,
        run: RunView,
        approval_token: str,
        payload_sha256: str,
    ) -> bool:
        """Consume an exact approval and claim its apply transition in one transaction."""

        with self._connect() as connection:
            # Acquire the write lock before reading either row. That keeps concurrent callers from
            # both observing an unconsumed approval and makes the approval + status transition one
            # indivisible claim.
            connection.execute("BEGIN IMMEDIATE")
            approval = connection.execute(
                "SELECT approval_token, payload_sha256, consumed_at FROM approvals "
                "WHERE run_id = ?",
                (run.id,),
            ).fetchone()
            if (
                not approval
                or approval["approval_token"] != approval_token
                or approval["payload_sha256"] != payload_sha256
                or approval["consumed_at"] is not None
            ):
                return False
            cursor = connection.execute(
                "UPDATE runs SET document = ?, updated_at = ? "
                "WHERE id = ? AND json_extract(document, '$.status') = ?",
                (run.model_dump_json(), _utc_iso(), run.id, RunStatus.APPROVED.value),
            )
            if cursor.rowcount != 1:
                return False
            consumed = connection.execute(
                "UPDATE approvals SET consumed_at = ? "
                "WHERE run_id = ? AND consumed_at IS NULL",
                (_utc_iso(), run.id),
            )
            return consumed.rowcount == 1

    def append_event(
        self,
        run_id: str,
        event_type: str,
        stage: str,
        message: str,
        payload: dict | None = None,
    ) -> AuditEvent:
        created_at = datetime.now(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO events(run_id, event_type, stage, message, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_type,
                    stage,
                    message,
                    json.dumps(payload or {}, sort_keys=True),
                    created_at.isoformat(),
                ),
            )
            event_id = int(cursor.lastrowid)
        return AuditEvent(
            id=event_id,
            run_id=run_id,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload or {},
            created_at=created_at,
        )

    def events_after(self, run_id: str, after_id: int = 0) -> list[AuditEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (run_id, after_id),
            ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                stage=row["stage"],
                message=row["message"],
                payload=json.loads(row["payload"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def approve(self, run_id: str, payload_sha256: str) -> tuple[str, datetime]:
        approval_token = secrets.token_urlsafe(32)
        approved_at = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO approvals(run_id, payload_sha256, approval_token, approved_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET payload_sha256=excluded.payload_sha256, "
                "approval_token=excluded.approval_token, approved_at=excluded.approved_at, "
                "consumed_at=NULL",
                (run_id, payload_sha256, approval_token, approved_at.isoformat()),
            )
        return approval_token, approved_at

    def consume_approval(self, run_id: str, approval_token: str, payload_sha256: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET consumed_at = ? "
                "WHERE run_id = ? AND approval_token = ? AND payload_sha256 = ? "
                "AND consumed_at IS NULL",
                (_utc_iso(), run_id, approval_token, payload_sha256),
            )
            return cursor.rowcount == 1

    def save_artifact(self, run_id: str, path: Path, sha256: str) -> None:
        content = path.read_bytes()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO artifacts"
                "(run_id, path, filename, content, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET path=excluded.path, "
                "filename=excluded.filename, content=excluded.content, "
                "sha256=excluded.sha256, created_at=excluded.created_at",
                (run_id, str(path), path.name, content, sha256, _utc_iso()),
            )

    def artifact(self, run_id: str) -> ArtifactRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path, filename, content, sha256 FROM artifacts WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        content = row["content"]
        path = Path(row["path"])
        if content is None:
            if not path.is_file():
                return None
            content = path.read_bytes()
        return ArtifactRecord(
            filename=row["filename"] or path.name,
            sha256=row["sha256"],
            content=bytes(content),
        )
