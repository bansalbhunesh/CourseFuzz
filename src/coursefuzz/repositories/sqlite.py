from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from coursefuzz.domain.models import AuditEvent, RunView


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class RunRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    document TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def create(self, run: RunView, idempotency_key: str) -> tuple[RunView, bool]:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT document FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                return RunView.model_validate_json(existing["document"]), False
            now = _utc_iso()
            connection.execute(
                "INSERT INTO runs(id, idempotency_key, document, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run.id, idempotency_key, run.model_dump_json(), now, now),
            )
        return run, True

    def get(self, run_id: str) -> RunView | None:
        with self._connect() as connection:
            row = connection.execute("SELECT document FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunView.model_validate_json(row["document"]) if row else None

    def save(self, run: RunView) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET document = ?, updated_at = ? WHERE id = ?",
                (run.model_dump_json(), _utc_iso(), run.id),
            )
            if cursor.rowcount != 1:
                raise KeyError(run.id)

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
            row = connection.execute(
                "SELECT * FROM approvals WHERE run_id = ?", (run_id,)
            ).fetchone()
            if (
                not row
                or row["approval_token"] != approval_token
                or row["payload_sha256"] != payload_sha256
            ):
                return False
            if row["consumed_at"] is None:
                connection.execute(
                    "UPDATE approvals SET consumed_at = ? WHERE run_id = ?",
                    (_utc_iso(), run_id),
                )
            return True

    def save_artifact(self, run_id: str, path: Path, sha256: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO artifacts(run_id, path, sha256, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET path=excluded.path, sha256=excluded.sha256, "
                "created_at=excluded.created_at",
                (run_id, str(path), sha256, _utc_iso()),
            )

    def artifact(self, run_id: str) -> tuple[Path, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path, sha256 FROM artifacts WHERE run_id = ?", (run_id,)
            ).fetchone()
        return (Path(row["path"]), row["sha256"]) if row else None
