"""Public-bundle, candidate-sealing, and hidden-scoring contracts for the real corpus."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from evaluations.real_corpus import (
    CorpusManifest,
    TaskReceipt,
    canonical_json,
    selected_source,
    sha256_bytes,
    sha256_json,
    sha256_text,
    verify_cached_row,
)


class PublicTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    upstream_name: str
    description: str
    public_tests: dict[str, list[str]]
    source: str
    difficulty: int
    time_limit: dict[str, int]
    memory_limit_bytes: int
    public_context_sha256: str


class Candidate(BaseModel):
    """One generator proposal. No expected-output field exists by design."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    generator: str = Field(min_length=1, max_length=80)
    ordinal: int = Field(ge=1)
    stdin: str = Field(max_length=1_000_000)
    rationale: str = Field(default="", max_length=2_000)


class CandidateReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    corpus_id: str
    selection_sha256: str
    public_bundle_sha256: str
    candidates_sha256: str
    budget_per_task: int = Field(gt=0)
    generators: tuple[str, ...]
    task_count: int = Field(gt=0)
    candidate_count: int = Field(gt=0)


class ProgramExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["completed", "timed_out", "runtime_error", "output_limit"]
    stdout: str = ""
    wall_ms: int = Field(ge=0)


class StdinExecutor(Protocol):
    """Adapter supplied by the invocation/execution phase; never implemented with host exec here."""

    runtime_identity: str

    def execute(self, source: str, stdin: str, task: TaskReceipt) -> ProgramExecution: ...


class ScoreAggregate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generator: str
    tasks: int
    candidate_count: int
    wrong_programs: int
    killed_wrong_programs: int
    defect_recall_percent: float
    mutation_score_percent: float
    defect_recall_ci95_percent: tuple[float, float]
    accepted_controls: int
    false_killed_controls: int
    false_kill_rate_percent: float
    false_kill_ci95_percent: tuple[float, float]
    abstained_candidates: int
    abstention_rate_percent: float
    queries_to_first_finding_median: float | None
    total_executions: int
    total_wall_ms: int


class TaskScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    generator: str
    candidate_count: int
    wrong_programs: int
    killed_wrong_programs: int
    defect_recall_percent: float
    accepted_controls: int
    false_killed_controls: int
    false_kill_rate_percent: float
    abstained_candidates: int
    queries_to_first_finding: int | None
    total_executions: int
    total_wall_ms: int


def _public_context(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": row["name"],
        "description": row["description"],
        "public_tests": row["public_tests"],
        "source": row["source"],
        "difficulty": row["difficulty"],
        "time_limit": row["time_limit"],
        "memory_limit_bytes": row["memory_limit_bytes"],
    }


def build_public_bundle(
    manifest: CorpusManifest, rows: Mapping[int, Mapping[str, Any]], output_path: Path
) -> str:
    """Write provider-visible fields only and return the bundle SHA-256."""

    public: list[PublicTask] = []
    for task in manifest.tasks:
        row = rows[task.global_row_index]
        verify_cached_row(task, row)
        context = _public_context(row)
        if sha256_json(context) != task.public_context_sha256:
            raise ValueError(f"{task.task_id}: public-context receipt changed")
        public.append(
            PublicTask(
                task_id=task.task_id,
                upstream_name=row["name"],
                description=row["description"],
                public_tests=row["public_tests"],
                source=task.source,
                difficulty=int(row["difficulty"]),
                time_limit=row["time_limit"],
                memory_limit_bytes=int(row["memory_limit_bytes"]),
                public_context_sha256=task.public_context_sha256,
            )
        )
    serialized = "".join(canonical_json(item.model_dump(mode="json")) + "\n" for item in public)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    return sha256_text(serialized)


def _read_candidates(path: Path) -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidates.append(Candidate.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"invalid candidate on line {line_number}: {error}") from error
    return tuple(candidates)


def seal_candidates(
    manifest: CorpusManifest,
    public_bundle_path: Path,
    candidates_path: Path,
    receipt_path: Path,
    *,
    budget_per_task: int,
) -> CandidateReceipt:
    """Freeze proposals before any scorer-only state is opened."""

    candidates = _read_candidates(candidates_path)
    task_ids = {task.task_id for task in manifest.tasks}
    generators = tuple(sorted({candidate.generator for candidate in candidates}))
    if not generators:
        raise ValueError("candidate file contains no generators")
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.task_id not in task_ids:
            raise ValueError(f"candidate references unknown task {candidate.task_id}")
        grouped[(candidate.task_id, candidate.generator)].append(candidate)
    for key, values in grouped.items():
        ordinals = {item.ordinal for item in values}
        inputs = {item.stdin for item in values}
        if (
            len(values) != budget_per_task
            or len(ordinals) != len(values)
            or len(inputs) != len(values)
        ):
            raise ValueError(
                f"{key}: candidates must be unique and exactly match the shared budget"
            )
    expected_groups = {(task_id, generator) for task_id in task_ids for generator in generators}
    if set(grouped) != expected_groups:
        raise ValueError("every generator must provide the same budget for every task")

    candidate_bytes = candidates_path.read_bytes()
    receipt = CandidateReceipt(
        corpus_id=manifest.corpus_id,
        selection_sha256=manifest.selection_sha256,
        public_bundle_sha256=sha256_bytes(public_bundle_path.read_bytes()),
        candidates_sha256=sha256_bytes(candidate_bytes),
        budget_per_task=budget_per_task,
        generators=generators,
        task_count=len(task_ids),
        candidate_count=len(candidates),
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def verify_candidate_receipt(
    manifest: CorpusManifest,
    public_bundle_path: Path,
    candidates_path: Path,
    receipt_path: Path,
) -> CandidateReceipt:
    receipt = CandidateReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.corpus_id != manifest.corpus_id
        or receipt.selection_sha256 != manifest.selection_sha256
    ):
        raise ValueError("candidate receipt belongs to a different frozen corpus")
    if receipt.public_bundle_sha256 != sha256_bytes(public_bundle_path.read_bytes()):
        raise ValueError("public bundle changed after candidate generation")
    if receipt.candidates_sha256 != sha256_bytes(candidates_path.read_bytes()):
        raise ValueError("candidate file changed after sealing")
    return receipt


def _normalized_output(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").split("\n")).rstrip()


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return (round(max(0.0, centre - margin) * 100, 2), round(min(1.0, centre + margin) * 100, 2))


def _percent(value: int, total: int) -> float:
    return round(value * 100 / total, 2) if total else 0.0


def score_candidates(
    manifest: CorpusManifest,
    public_bundle_path: Path,
    candidates_path: Path,
    receipt_path: Path,
    rows: Mapping[int, Mapping[str, Any]],
    executor: StdinExecutor,
) -> dict[str, Any]:
    """Score a sealed file. Receipt verification happens before hidden rows are touched."""

    receipt = verify_candidate_receipt(
        manifest, public_bundle_path, candidates_path, receipt_path
    )
    candidates = _read_candidates(candidates_path)
    by_generator: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_generator[candidate.generator].append(candidate)
    tasks = {task.task_id: task for task in manifest.tasks}
    aggregates: list[ScoreAggregate] = []
    task_scores: list[TaskScore] = []

    for generator, proposals in sorted(by_generator.items()):
        killed: set[tuple[str, str]] = set()
        false_killed: set[tuple[str, str]] = set()
        abstentions = 0
        executions = 0
        wall_ms = 0
        first_queries: list[int] = []
        proposals.sort(key=lambda item: (item.task_id, item.ordinal))
        by_task: dict[str, list[Candidate]] = defaultdict(list)
        for proposal in proposals:
            by_task[proposal.task_id].append(proposal)

        for task_id, task_proposals in by_task.items():
            task = tasks[task_id]
            row = rows[task.global_row_index]
            verify_cached_row(task, row)
            first_query: int | None = None
            task_killed: set[str] = set()
            task_false_killed: set[str] = set()
            task_abstentions = 0
            task_executions = 0
            task_wall_ms = 0
            for query_number, proposal in enumerate(task_proposals, start=1):
                oracle_outputs: list[str] = []
                oracle_ok = True
                for oracle in task.oracle_programs:
                    result = executor.execute(
                        selected_source(row, "oracle", oracle), proposal.stdin, task
                    )
                    executions += 1
                    task_executions += 1
                    wall_ms += result.wall_ms
                    task_wall_ms += result.wall_ms
                    if result.outcome != "completed":
                        oracle_ok = False
                    else:
                        oracle_outputs.append(_normalized_output(result.stdout))
                if not oracle_ok or len(set(oracle_outputs)) != 1:
                    abstentions += 1
                    task_abstentions += 1
                    continue
                expected = oracle_outputs[0]
                for control in task.accepted_controls:
                    result = executor.execute(
                        selected_source(row, "control", control), proposal.stdin, task
                    )
                    executions += 1
                    task_executions += 1
                    wall_ms += result.wall_ms
                    task_wall_ms += result.wall_ms
                    if (
                        result.outcome != "completed"
                        or _normalized_output(result.stdout) != expected
                    ):
                        false_killed.add((task_id, control.normalized_sha256))
                        task_false_killed.add(control.normalized_sha256)
                killed_this_query = False
                for wrong in task.wrong_programs:
                    result = executor.execute(
                        selected_source(row, "wrong", wrong), proposal.stdin, task
                    )
                    executions += 1
                    task_executions += 1
                    wall_ms += result.wall_ms
                    task_wall_ms += result.wall_ms
                    if (
                        result.outcome != "completed"
                        or _normalized_output(result.stdout) != expected
                    ):
                        killed.add((task_id, wrong.normalized_sha256))
                        task_killed.add(wrong.normalized_sha256)
                        killed_this_query = True
                if killed_this_query and first_query is None:
                    first_query = query_number
            if first_query is not None:
                first_queries.append(first_query)
            task_scores.append(
                TaskScore(
                    task_id=task_id,
                    generator=generator,
                    candidate_count=len(task_proposals),
                    wrong_programs=len(task.wrong_programs),
                    killed_wrong_programs=len(task_killed),
                    defect_recall_percent=_percent(len(task_killed), len(task.wrong_programs)),
                    accepted_controls=len(task.accepted_controls),
                    false_killed_controls=len(task_false_killed),
                    false_kill_rate_percent=_percent(
                        len(task_false_killed), len(task.accepted_controls)
                    ),
                    abstained_candidates=task_abstentions,
                    queries_to_first_finding=first_query,
                    total_executions=task_executions,
                    total_wall_ms=task_wall_ms,
                )
            )

        wrong_total = sum(len(task.wrong_programs) for task in manifest.tasks)
        controls_total = sum(len(task.accepted_controls) for task in manifest.tasks)
        ordered_queries = sorted(first_queries)
        median_query: float | None = None
        if ordered_queries:
            middle = len(ordered_queries) // 2
            if len(ordered_queries) % 2:
                median_query = float(ordered_queries[middle])
            else:
                median_query = (ordered_queries[middle - 1] + ordered_queries[middle]) / 2
        aggregates.append(
            ScoreAggregate(
                generator=generator,
                tasks=len(manifest.tasks),
                candidate_count=len(proposals),
                wrong_programs=wrong_total,
                killed_wrong_programs=len(killed),
                defect_recall_percent=_percent(len(killed), wrong_total),
                mutation_score_percent=_percent(len(killed), wrong_total),
                defect_recall_ci95_percent=_wilson(len(killed), wrong_total),
                accepted_controls=controls_total,
                false_killed_controls=len(false_killed),
                false_kill_rate_percent=_percent(len(false_killed), controls_total),
                false_kill_ci95_percent=_wilson(len(false_killed), controls_total),
                abstained_candidates=abstentions,
                abstention_rate_percent=_percent(abstentions, len(proposals)),
                queries_to_first_finding_median=median_query,
                total_executions=executions,
                total_wall_ms=wall_ms,
            )
        )

    return {
        "benchmark": manifest.corpus_id,
        "selection_sha256": manifest.selection_sha256,
        "candidate_receipt": receipt.model_dump(mode="json"),
        "runtime": executor.runtime_identity,
        "aggregates": [item.model_dump(mode="json") for item in aggregates],
        "tasks": [item.model_dump(mode="json") for item in task_scores],
        "claim_status": "provisional-until-second-review-and-isolated-replay",
    }
