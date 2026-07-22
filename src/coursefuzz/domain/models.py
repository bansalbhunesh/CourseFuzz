from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JsonAtom = str | int | float | bool


def _validate_relative_directory(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("test_directory must be a safe relative POSIX path")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("test_directory contains an empty path segment")
    return path.as_posix().rstrip("/")


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    APPLYING = "applying"
    EXTERNAL_CI_PENDING = "external_ci_pending"
    VERIFIED = "verified"
    NO_ACTION_REQUIRED = "no_action_required"
    EXTERNAL_CI_FAILED = "external_ci_failed"
    FAILED = "failed"


class TestCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    inputs: tuple[int, ...]
    expected: JsonAtom | None = None
    label: str
    source: Literal["instructor", "gpt-5.6", "deterministic", "minimized"] = "instructor"


class ProgramVariant(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    misconception: str = ""
    source: str


class LocalArtifactDestination(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["local_artifact"] = "local_artifact"
    test_directory: str = "verified_tests"

    @model_validator(mode="after")
    def validate_directory(self) -> LocalArtifactDestination:
        object.__setattr__(
            self,
            "test_directory",
            _validate_relative_directory(self.test_directory),
        )
        return self


class GitHubPullRequestDestination(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["github_pull_request"] = "github_pull_request"
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str = Field(default="main", min_length=1, max_length=200)
    test_directory: str = "tests/coursefuzz"

    @model_validator(mode="after")
    def validate_destination(self) -> GitHubPullRequestDestination:
        if (
            self.base_branch.startswith("/")
            or self.base_branch.endswith("/")
            or ".." in self.base_branch
            or self.base_branch.endswith(".lock")
        ):
            raise ValueError("base_branch is not a safe Git reference")
        object.__setattr__(
            self,
            "test_directory",
            _validate_relative_directory(self.test_directory),
        )
        return self


DestinationConfig = LocalArtifactDestination | GitHubPullRequestDestination


class AssignmentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = ""
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
    destination: DestinationConfig = Field(default_factory=LocalArtifactDestination)


class ProgramSourceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=16_384)
    misconception: str = Field(default="none", min_length=1, max_length=500)


class InstructorTestInput(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    inputs: tuple[int, ...] = Field(min_length=1, max_length=6)
    expected: JsonAtom
    label: str = Field(min_length=1, max_length=120)


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=10, max_length=2_000)
    entrypoint: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class AssignmentGenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    prompt: str = Field(min_length=3, max_length=1_000)
    input_names: tuple[str, ...] = Field(min_length=1, max_length=6)
    domain_min: int = Field(ge=-1_000, le=1_000)
    domain_max: int = Field(ge=-1_000, le=1_000)
    reference: ProgramSourceInput
    accepted_solutions: tuple[ProgramSourceInput, ...] = Field(min_length=1, max_length=7)
    misconception_programs: tuple[ProgramSourceInput, ...] = Field(min_length=1, max_length=64)
    instructor_tests: tuple[InstructorTestInput, ...] = Field(min_length=1, max_length=100)
    destination: DestinationConfig = Field(default_factory=LocalArtifactDestination)

    @model_validator(mode="after")
    def validate_contract(self) -> AssignmentCreate:
        if self.domain_min > self.domain_max:
            raise ValueError("domain_min must be less than or equal to domain_max")
        if len(set(self.input_names)) != len(self.input_names):
            raise ValueError("input_names must be unique")
        if any(not name.isidentifier() for name in self.input_names):
            raise ValueError("every input name must be a valid Python identifier")
        if any(len(test.inputs) != len(self.input_names) for test in self.instructor_tests):
            raise ValueError("every instructor test must match the entrypoint arity")
        if any(
            value < self.domain_min or value > self.domain_max
            for test in self.instructor_tests
            for value in test.inputs
        ):
            raise ValueError("instructor test inputs must stay inside the declared domain")
        return self


class GitHubImportProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    installation_id: int
    repository: str
    commit_sha: str
    branch: str | None = None
    webhook_delivery_id: str | None = None


class AssignmentSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    snapshot_sha256: str
    provenance: Literal["seeded", "manual", "github_import"]
    github_provenance: GitHubImportProvenance | None = None
    created_at: datetime
    spec: AssignmentSpec


class AssignmentSummary(BaseModel):
    id: str
    snapshot_sha256: str
    provenance: Literal["seeded", "manual", "github_import"]
    github_provenance: GitHubImportProvenance | None = None
    created_at: datetime
    title: str
    summary: str
    entrypoint: str
    language: Literal["python"] = "python"
    instructor_test_count: int
    misconception_program_count: int
    accepted_solution_count: int


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
    # Which candidate generator produced this input, when a scheduler composes several. Optional so
    # the single-provider path and existing serialized runs are unchanged.
    generator: str | None = None


class HypothesisVerdict(BaseModel):
    hypothesis: AttackHypothesis
    status: Literal["rejected", "verified"]
    reason: str
    expected: JsonAtom | None = None
    actual: JsonAtom | None = None
    killed_mutants: tuple[str, ...] = ()


class MutationMetrics(BaseModel):
    total_mutants: int
    killed_mutants: int
    surviving_mutants: int
    mutation_score: float
    accepted_solution_pass_rate: float


class OracleDecision(BaseModel):
    """How the expected output for one input was established (or why the oracle abstained).

    Makes the truth source auditable: a resolved decision records which independent sources agreed
    and how; an abstention records why. Bound into the candidate so approval covers provenance.
    """

    model_config = ConfigDict(frozen=True)

    expected: JsonAtom | None = None
    decision: Literal["resolved", "abstained"]
    provenance: str
    evidence_sources: tuple[str, ...] = ()
    quorum: int = 0
    abstention_reason: str | None = None

    @property
    def resolved(self) -> bool:
        return self.decision == "resolved"


class CandidatePatch(BaseModel):
    id: str
    test: TestCase
    observed_actual: JsonAtom | None = None
    rationale: str
    target_mutants: tuple[str, ...]
    payload_sha256: str
    pytest_source: str
    oracle: OracleDecision | None = None
    target: PatchTarget = Field(
        default_factory=lambda: PatchTarget(
            kind="local_artifact",
            path="verified_tests/test_generated.py",
        )
    )


class PatchTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["local_artifact", "github_pull_request"]
    path: str
    repository: str | None = None
    base_branch: str | None = None
    base_commit_sha: str | None = None
    head_branch: str | None = None


class ActionReceipt(BaseModel):
    kind: Literal["local_artifact", "github_pull_request"]
    path: str
    artifact_sha256: str
    read_back_verified: bool
    external_url: str | None = None
    repository: str | None = None
    base_commit_sha: str | None = None
    commit_sha: str | None = None
    pull_request_number: int | None = None
    # External (target-repository) CI read-back. A GitHub action is only fully verified once byte
    # read-back AND the destination's own CI conclude. external_ci_verified stays False until then.
    external_ci_started_at: datetime | None = None
    external_ci_url: str | None = None
    external_ci_conclusion: str | None = None
    external_ci_completed_at: datetime | None = None
    external_ci_verified: bool = False


class AnalysisResult(BaseModel):
    before: MutationMetrics
    projected_after: MutationMetrics
    survivors_before: tuple[str, ...]
    hypothesis_verdicts: tuple[HypothesisVerdict, ...]
    candidate: CandidatePatch | None = None
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
    assignment_snapshot_sha256: str | None = None
    status: RunStatus
    mode: Literal["live-gpt-5.6", "deterministic-fallback"]
    created_at: datetime
    updated_at: datetime
    analysis: AnalysisResult | None = None
    approval_payload_sha256: str | None = None
    artifact_sha256: str | None = None
    action_receipt: ActionReceipt | None = None
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


class EvidenceContent(BaseModel):
    """The hashed body of an evidence bundle: everything a third party needs to re-verify a run.

    Deterministically serialized so ``EvidenceBundle.bundle_sha256`` can be recomputed offline and
    compared byte-for-byte. Excludes envelope metadata (generation time, the hash itself) so the
    digest depends only on the evidence, not on when the bundle was produced.
    """

    model_config = ConfigDict(frozen=True)

    run: RunView
    assignment_snapshot_sha256: str | None = None
    oracle_evidence: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    audit_events: tuple[AuditEvent, ...] = ()


class EvidenceBundle(BaseModel):
    """A self-contained, independently re-hashable record of one run's evidence.

    A judge downloads this, recomputes SHA-256 over the canonical JSON of ``content`` (sorted keys,
    compact separators), and confirms it equals ``bundle_sha256`` — proving the assignment snapshot,
    oracle provenance, approval, destination read-back receipt, and ordered audit trail were not
    altered after the fact.
    """

    model_config = ConfigDict(frozen=True)

    bundle_version: Literal["coursefuzz-evidence-v1"] = "coursefuzz-evidence-v1"
    generated_at: datetime
    bundle_sha256: str
    content: EvidenceContent
