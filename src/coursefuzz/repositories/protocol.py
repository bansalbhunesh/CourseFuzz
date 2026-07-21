from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from coursefuzz.domain.models import AssignmentSnapshot, AuditEvent, RunView
from coursefuzz.repositories.types import ArtifactRecord
from coursefuzz.security.access import LOCAL_TENANT


class Repository(Protocol):
    backend_name: str

    def create_assignment(
        self,
        snapshot: AssignmentSnapshot,
        tenant_id: str = LOCAL_TENANT,
    ) -> tuple[AssignmentSnapshot, bool]: ...

    def get_assignment(
        self,
        assignment_id: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> AssignmentSnapshot | None: ...

    def list_assignments(
        self,
        tenant_id: str = LOCAL_TENANT,
    ) -> list[AssignmentSnapshot]: ...

    def create(
        self,
        run: RunView,
        idempotency_key: str,
        owner_id: str = LOCAL_TENANT,
    ) -> tuple[RunView, bool]: ...

    def get(self, run_id: str, owner_id: str = LOCAL_TENANT) -> RunView | None: ...

    def list_runs(
        self,
        assignment_id: str | None = None,
        owner_id: str = LOCAL_TENANT,
    ) -> list[RunView]: ...

    def list_recoverable_runs(self, limit: int = 10) -> list[tuple[str, RunView]]: ...

    def save(self, run: RunView) -> None: ...

    def append_event(
        self,
        run_id: str,
        event_type: str,
        stage: str,
        message: str,
        payload: dict | None = None,
    ) -> AuditEvent: ...

    def events_after(self, run_id: str, after_id: int = 0) -> list[AuditEvent]: ...

    def approve(self, run_id: str, payload_sha256: str) -> tuple[str, datetime]: ...

    def consume_approval(
        self,
        run_id: str,
        approval_token: str,
        payload_sha256: str,
    ) -> bool: ...

    def save_artifact(self, run_id: str, path: Path, sha256: str) -> None: ...

    def artifact(self, run_id: str) -> ArtifactRecord | None: ...
