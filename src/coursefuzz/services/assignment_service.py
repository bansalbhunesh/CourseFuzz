from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from coursefuzz.adapters.runner import validate_source
from coursefuzz.adapters.sandbox import SubprocessPythonSandbox
from coursefuzz.domain.models import (
    AssignmentCreate,
    AssignmentSnapshot,
    AssignmentSpec,
    AssignmentSummary,
    ProgramSourceInput,
    ProgramVariant,
    TestCase,
)
from coursefuzz.repositories.protocol import Repository
from coursefuzz.security.access import GLOBAL_TENANT, LOCAL_TENANT


def _normalize_source(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def _program_id(role: str, index: int, source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    return f"{role}-{index + 1}-{digest}"


def _snapshot_sha256(spec: AssignmentSpec) -> str:
    payload = spec.model_dump(mode="json", exclude={"id"})
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class AssignmentService:
    def __init__(self, repository: Repository, sandbox: SubprocessPythonSandbox) -> None:
        self.repository = repository
        self.sandbox = sandbox

    def seed(self, spec: AssignmentSpec) -> AssignmentSnapshot:
        snapshot = AssignmentSnapshot(
            id=spec.id,
            snapshot_sha256=_snapshot_sha256(spec),
            provenance="seeded",
            created_at=datetime.now(UTC),
            spec=spec,
        )
        stored, _ = self.repository.create_assignment(snapshot, GLOBAL_TENANT)
        return stored

    def create(
        self,
        payload: AssignmentCreate,
        tenant_id: str = LOCAL_TENANT,
    ) -> tuple[AssignmentSnapshot, bool]:
        spec = self._build_spec(payload)
        self._preflight(spec)
        digest = _snapshot_sha256(spec)
        assignment_id = f"asg_{digest[:16]}"
        snapshot = AssignmentSnapshot(
            id=assignment_id,
            snapshot_sha256=digest,
            provenance="manual",
            created_at=datetime.now(UTC),
            spec=spec.model_copy(update={"id": assignment_id}),
        )
        return self.repository.create_assignment(snapshot, tenant_id)

    def require(
        self,
        assignment_id: str,
        tenant_id: str = LOCAL_TENANT,
    ) -> AssignmentSnapshot:
        snapshot = self.repository.get_assignment(assignment_id, tenant_id)
        if not snapshot:
            raise KeyError(assignment_id)
        return snapshot

    def list(self, tenant_id: str = LOCAL_TENANT) -> list[AssignmentSummary]:
        return [
            self._summary(item) for item in self.repository.list_assignments(tenant_id)
        ]

    @staticmethod
    def _variant(role: str, index: int, item: ProgramSourceInput) -> ProgramVariant:
        source = _normalize_source(item.source)
        return ProgramVariant(
            id=_program_id(role, index, source),
            title=item.title,
            misconception=item.misconception,
            source=source,
        )

    def _build_spec(self, payload: AssignmentCreate) -> AssignmentSpec:
        reference_source = _normalize_source(payload.reference.source)
        reference = ProgramVariant(
            id=_program_id("reference", 0, reference_source),
            title=payload.reference.title,
            misconception="none",
            source=reference_source,
        )
        accepted = tuple(
            self._variant("accepted", index, item)
            for index, item in enumerate(payload.accepted_solutions)
        )
        mutants = tuple(
            self._variant("misconception", index, item)
            for index, item in enumerate(payload.misconception_programs)
        )
        tests = tuple(
            TestCase(
                inputs=item.inputs,
                expected=item.expected,
                label=item.label,
                source="instructor",
            )
            for item in payload.instructor_tests
        )
        return AssignmentSpec(
            id="pending-content-address",
            title=payload.title,
            summary=payload.summary,
            entrypoint=payload.entrypoint,
            input_names=payload.input_names,
            domain_min=payload.domain_min,
            domain_max=payload.domain_max,
            reference=reference,
            accepted_solutions=(reference, *accepted),
            mutants=mutants,
            instructor_tests=tests,
            destination=payload.destination,
        )

    def _preflight(self, spec: AssignmentSpec) -> None:
        sources = [item.source for item in spec.accepted_solutions]
        if len(set(sources)) != len(sources):
            raise ValueError("reference and accepted controls must have distinct source code")

        for program in (*spec.accepted_solutions, *spec.mutants):
            try:
                validate_source(program.source, spec.entrypoint)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"{program.title}: {exc}") from exc

        for program in spec.accepted_solutions:
            execution = self.sandbox.run_suite(program, spec.entrypoint, spec.instructor_tests)
            if not execution.all_passed:
                detail = execution.error or f"failed {execution.failed} instructor tests"
                raise ValueError(f"accepted control '{program.title}' {detail}")

    @staticmethod
    def _summary(snapshot: AssignmentSnapshot) -> AssignmentSummary:
        spec = snapshot.spec
        return AssignmentSummary(
            id=snapshot.id,
            snapshot_sha256=snapshot.snapshot_sha256,
            provenance=snapshot.provenance,
            created_at=snapshot.created_at,
            title=spec.title,
            summary=spec.summary,
            entrypoint=spec.entrypoint,
            instructor_test_count=len(spec.instructor_tests),
            misconception_program_count=len(spec.mutants),
            accepted_solution_count=len(spec.accepted_solutions),
        )
