from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from coursefuzz.domain.models import AssignmentSnapshot, AuditEvent, RunStatus, RunView
from coursefuzz.repositories.blob_storage import BlobStorage
from coursefuzz.repositories.types import ArtifactRecord
from coursefuzz.security.access import GLOBAL_TENANT, LOCAL_TENANT


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class PostgresRunRepository:
    """Durable repository for hosted CourseFuzz deployments."""

    backend_name = "postgres"

    def __init__(self, dsn: str, blob_storage: BlobStorage | None = None) -> None:
        self.dsn = dsn
        self.blob_storage = blob_storage
        self._run_migrations()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    def _run_migrations(self) -> None:
        from coursefuzz.repositories.migrator import Migrator

        migrations_dir = Path(__file__).parent / "migrations"
        migrator = Migrator(self.dsn, migrations_dir)
        migrator.migrate()

    def create_assignment(
        self,
        snapshot: AssignmentSnapshot,
        tenant_id: str = LOCAL_TENANT,
    ) -> tuple[AssignmentSnapshot, bool]:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT document FROM assignments WHERE snapshot_sha256 = %s",
                (snapshot.snapshot_sha256,),
            ).fetchone()
            if existing:
                stored = AssignmentSnapshot.model_validate_json(existing["document"])
                connection.execute(
                    "INSERT INTO assignment_access"
                    "(assignment_id, tenant_id, granted_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (stored.id, tenant_id, _utc_iso()),
                )
                return stored, False
            existing_id = connection.execute(
                "SELECT document FROM assignments WHERE id = %s",
                (snapshot.id,),
            ).fetchone()
            if existing_id:
                stored = AssignmentSnapshot.model_validate_json(existing_id["document"])
                if stored.provenance != "seeded" or snapshot.provenance != "seeded":
                    raise ValueError("Assignment ID collision for an immutable manual snapshot")
                connection.execute(
                    "UPDATE assignments SET snapshot_sha256 = %s, document = %s, "
                    "created_at = %s WHERE id = %s",
                    (
                        snapshot.snapshot_sha256,
                        snapshot.model_dump_json(),
                        snapshot.created_at.isoformat(),
                        snapshot.id,
                    ),
                )
                connection.execute(
                    "INSERT INTO assignment_access"
                    "(assignment_id, tenant_id, granted_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (snapshot.id, tenant_id, _utc_iso()),
                )
                return snapshot, False
            connection.execute(
                "INSERT INTO assignments(id, snapshot_sha256, document, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (
                    snapshot.id,
                    snapshot.snapshot_sha256,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO assignment_access(assignment_id, tenant_id, granted_at) "
                "VALUES (%s, %s, %s)",
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
                "WHERE assignments.id = %s AND assignment_access.tenant_id IN (%s, %s) "
                "ORDER BY (assignment_access.tenant_id = %s) DESC LIMIT 1",
                (assignment_id, tenant_id, GLOBAL_TENANT, tenant_id),
            ).fetchone()
        return AssignmentSnapshot.model_validate_json(row["document"]) if row else None

    def list_assignments(self, tenant_id: str = LOCAL_TENANT) -> list[AssignmentSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT assignments.document, assignments.created_at "
                "FROM assignments "
                "JOIN assignment_access ON assignment_access.assignment_id = assignments.id "
                "WHERE assignment_access.tenant_id IN (%s, %s) "
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
        with self._connect() as connection, connection.transaction():
            existing = connection.execute(
                "SELECT document FROM runs WHERE idempotency_key = %s",
                (scoped_idempotency_key,),
            ).fetchone()
            if existing:
                return RunView.model_validate_json(existing["document"]), False
            now = _utc_iso()
            connection.execute(
                "INSERT INTO runs"
                "(id, idempotency_key, document, created_at, updated_at, owner_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (run.id, scoped_idempotency_key, run.model_dump_json(), now, now, owner_id),
            )
            connection.execute(
                "INSERT INTO outbox_events (run_id, event_type, payload) VALUES (%s, %s, %s)",
                (run.id, "run_created", "{}"),
            )
        return run, True

    def get(self, run_id: str, owner_id: str = LOCAL_TENANT) -> RunView | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM runs WHERE id = %s AND owner_id = %s",
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
                "SELECT document FROM runs WHERE owner_id = %s ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        runs = [RunView.model_validate_json(row["document"]) for row in rows]
        if assignment_id is not None:
            runs = [run for run in runs if run.assignment_id == assignment_id]
        return runs

    def list_recoverable_runs(self, limit: int = 10) -> list[tuple[str, RunView]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, document FROM runs "
                "WHERE document::jsonb->>'status' IN (%s, %s, %s, %s) "
                "ORDER BY updated_at ASC LIMIT %s",
                ("queued", "analyzing", "applying", "external_ci_pending", limit),
            ).fetchall()
        return [(row["owner_id"], RunView.model_validate_json(row["document"])) for row in rows]

    def claim_run_for_worker(self, worker_id: str, limit: int = 1) -> list[tuple[str, RunView]]:
        """Claims available runs for a worker using SKIP LOCKED."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE runs
                SET leased_by = %s, leased_until = NOW() + INTERVAL '5 minutes'
                WHERE id IN (
                    SELECT id FROM runs
                    WHERE (leased_until IS NULL OR leased_until < NOW())
                      AND document::jsonb->>'status' IN (
                          'queued', 'analyzing', 'applying', 'external_ci_pending'
                      )
                    ORDER BY updated_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                RETURNING owner_id, document
                """,
                (worker_id, limit),
            ).fetchall()
        return [(row["owner_id"], RunView.model_validate_json(row["document"])) for row in rows]

    def heartbeat_lease(self, run_id: str, worker_id: str) -> bool:
        """Extends a worker's lease on a run."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET leased_until = NOW() + INTERVAL '5 minutes'
                WHERE id = %s AND leased_by = %s
                """,
                (run_id, worker_id),
            )
            return cursor.rowcount > 0

    def release_lease(self, run_id: str, worker_id: str) -> None:
        """Releases a worker's lease."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET leased_by = NULL, leased_until = NULL
                WHERE id = %s AND leased_by = %s
                """,
                (run_id, worker_id),
            )

    def save(self, run: RunView) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE runs SET document = %s, updated_at = %s WHERE id = %s",
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
            approval = connection.execute(
                "SELECT approval_token, payload_sha256, consumed_at FROM approvals "
                "WHERE run_id = %s FOR UPDATE",
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
                "UPDATE runs SET document = %s, updated_at = %s "
                "WHERE id = %s AND document::jsonb->>'status' = %s",
                (run.model_dump_json(), _utc_iso(), run.id, RunStatus.APPROVED.value),
            )
            if cursor.rowcount != 1:
                return False
            consumed = connection.execute(
                "UPDATE approvals SET consumed_at = %s WHERE run_id = %s AND consumed_at IS NULL",
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
            row = connection.execute(
                "INSERT INTO events"
                "(run_id, event_type, stage, message, payload, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    run_id,
                    event_type,
                    stage,
                    message,
                    json.dumps(payload or {}, sort_keys=True),
                    created_at.isoformat(),
                ),
            ).fetchone()
        return AuditEvent(
            id=int(row["id"]),
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
                "SELECT * FROM events WHERE run_id = %s AND id > %s ORDER BY id ASC",
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
                "INSERT INTO approvals"
                "(run_id, payload_sha256, approval_token, approved_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "payload_sha256=EXCLUDED.payload_sha256, "
                "approval_token=EXCLUDED.approval_token, "
                "approved_at=EXCLUDED.approved_at, consumed_at=NULL",
                (run_id, payload_sha256, approval_token, approved_at.isoformat()),
            )
        return approval_token, approved_at

    def consume_approval(self, run_id: str, approval_token: str, payload_sha256: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "UPDATE approvals SET consumed_at = %s "
                "WHERE run_id = %s AND approval_token = %s AND payload_sha256 = %s "
                "AND consumed_at IS NULL "
                "RETURNING run_id",
                (_utc_iso(), run_id, approval_token, payload_sha256),
            ).fetchone()
        return row is not None

    def save_artifact(self, run_id: str, path: Path, sha256: str) -> None:
        content = path.read_bytes()
        uri = None
        if self.blob_storage:
            key = f"runs/{run_id}/artifacts/{path.name}"
            uri = self.blob_storage.put(key, content)

        with self._connect() as connection:
            if uri:
                connection.execute(
                    "INSERT INTO artifacts"
                    "(run_id, filename, uri, sha256, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT(run_id) DO UPDATE SET filename=EXCLUDED.filename, "
                    "uri=EXCLUDED.uri, sha256=EXCLUDED.sha256, "
                    "created_at=EXCLUDED.created_at",
                    (run_id, path.name, uri, sha256, _utc_iso()),
                )
            else:
                # Fallback to bytea
                connection.execute(
                    "INSERT INTO artifacts"
                    "(run_id, filename, content, sha256, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT(run_id) DO UPDATE SET filename=EXCLUDED.filename, "
                    "content=EXCLUDED.content, sha256=EXCLUDED.sha256, "
                    "created_at=EXCLUDED.created_at",
                    (run_id, path.name, content, sha256, _utc_iso()),
                )

    def artifact(self, run_id: str) -> ArtifactRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT filename, content, uri, sha256 FROM artifacts WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        if not row:
            return None

        content = None
        if row["uri"] and self.blob_storage:
            content = self.blob_storage.get(row["uri"])
        if content is None and row["content"] is not None:
            content = bytes(row["content"])

        if content is None:
            return None

        return ArtifactRecord(
            filename=row["filename"],
            sha256=row["sha256"],
            content=content,
        )
