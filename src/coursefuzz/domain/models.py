from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    APPLYING = "applying"
    VERIFIED = "verified"
    FAILED = "failed"


class TestCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    inputs: tuple[int, ...]
    expected: str | None = None
    label: str
    source: Literal["instructor", "gpt-5.6", "deterministic", "minimized"]


class ProgramVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    misconception: str
    source: str


class AssignmentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    summary: str
    entrypoint: str
    language: Literal["python"] = "python"
    input_names: tuple[str, ...]
    domain_min: int
    domain_max: int
    reference: ProgramVariant
    accepted_solutions: tuple[ProgramVariant, ...]
    mutants: tuple[ProgramVariant, ...]
    instructor_tests: tuple[TestCase, ...]


class SuiteExecution(BaseModel):
    program_id: str
    passed: int
    failed: int
    timed_out: bool = False
    error: str | None = None
    outputs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return not self.timed_out and self.error is None and self.failed == 0


class AttackHypothesis(BaseModel):
    id: str
    inputs: tuple[int, ...]
    rationale: str
    misconception: str
    provider: Literal["gpt-5.6", "deterministic-fallback"]


class HypothesisVerdict(BaseModel):
    hypothesis: AttackHypothesis
    status: Literal["rejected", "verified"]
    reason: str
    expected: str | None = None
    actual: str | None = None
    killed_mutants: tuple[str, ...] = ()


class MutationMetrics(BaseModel):
    total_mutants: int
    killed_mutants: int
    surviving_mutants: int
    mutation_score: float
    accepted_solution_pass_rate: float


class CandidatePatch(BaseModel):
    id: str
    test: TestCase
    observed_actual: str | None = None
    rationale: str
    target_mutants: tuple[str, ...]
    payload_sha256: str
    pytest_source: str


class AnalysisResult(BaseModel):
    before: MutationMetrics
    projected_after: MutationMetrics
    survivors_before: tuple[str, ...]
    hypothesis_verdicts: tuple[HypothesisVerdict, ...]
    candidate: CandidatePatch
    evidence: dict[str, Any]


class AuditEvent(BaseModel):
    id: int
    run_id: str
    event_type: str
    stage: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunView(BaseModel):
    id: str
    assignment_id: str
    status: RunStatus
    mode: Literal["live-gpt-5.6", "deterministic-fallback"]
    created_at: datetime
    updated_at: datetime
    analysis: AnalysisResult | None = None
    approval_payload_sha256: str | None = None
    artifact_sha256: str | None = None
    error: str | None = None


class RunCreate(BaseModel):
    assignment_id: str = "triangle-classifier"


class ApprovalRequest(BaseModel):
    payload_sha256: str


class ApprovalReceipt(BaseModel):
    run_id: str
    approval_token: str
    payload_sha256: str
    approved_at: datetime


class ApplyRequest(BaseModel):
    approval_token: str
