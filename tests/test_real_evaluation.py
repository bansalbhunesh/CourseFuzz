from __future__ import annotations

import json
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from evaluations.real_corpus import (
    ATCODER,
    CodeContestsSource,
    MetadataRecord,
    SelectionPolicy,
    canonical_json,
    collect_manifest,
    load_manifest,
)
from evaluations.real_scoring import (
    Candidate,
    ProgramExecution,
    build_public_bundle,
    score_candidates,
    seal_candidates,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(name: str = "p00001 Fixture") -> dict[str, Any]:
    accepted = (
        "accepted-alpha",
        "accepted-beta",
        "accepted-gamma",
    )
    wrong = ("wrong-alpha", "wrong-beta")
    return {
        "name": name,
        "description": "Read one token and print the fixture result.",
        "public_tests": {"input": ["public\n"], "output": ["ok\n"]},
        "private_tests": {"input": ["private\n"], "output": ["ok\n"]},
        "generated_tests": {"input": ["generated\n"], "output": ["ok\n"]},
        "source": ATCODER,
        "difficulty": 1,
        "solutions": {"language": [3, 3, 3], "solution": list(accepted)},
        "incorrect_solutions": {"language": [3, 3], "solution": list(wrong)},
        "cf_contest_id": 0,
        "cf_index": "",
        "cf_points": 0,
        "cf_rating": 0,
        "cf_tags": [],
        "is_description_translated": False,
        "untranslated_description": None,
        "time_limit": {"seconds": 1, "nanos": 0},
        "memory_limit_bytes": 64_000_000,
        "input_file": "",
        "output_file": "",
    }


class _FixtureSource:
    def __init__(self) -> None:
        self.raw = _row()
        self.rows_opened = 0

    def metadata(self) -> tuple[MetadataRecord, ...]:
        return (
            MetadataRecord(
                name=self.raw["name"],
                source=ATCODER,
                shard="fixture.parquet",
                global_row_index=7,
                public_tests=1,
                private_tests=1,
                generated_tests=1,
                python3_correct=3,
                python3_wrong=2,
                input_file="",
                output_file="",
            ),
            MetadataRecord(
                name="blocked source",
                source=2,
                shard="fixture.parquet",
                global_row_index=8,
                public_tests=1,
                private_tests=1,
                generated_tests=1,
                python3_correct=3,
                python3_wrong=2,
                input_file="",
                output_file="",
            ),
        )

    def row(self, global_row_index: int) -> Mapping[str, Any]:
        assert global_row_index == 7
        self.rows_opened += 1
        return self.raw


def _fixture_collection():
    source = _FixtureSource()
    policy = SelectionPolicy(
        minimum_hidden_tests=2,
        minimum_python3_correct=3,
        minimum_python3_wrong=2,
        tasks=1,
        oracle_programs=2,
        accepted_controls=1,
        wrong_programs=2,
    )
    manifest, exclusions = collect_manifest(source, policy)
    return source, manifest, exclusions


def _candidate_file(path: Path, task_id: str) -> None:
    candidates = (
        Candidate(task_id=task_id, generator="fixture", ordinal=1, stdin="safe\n"),
        Candidate(task_id=task_id, generator="fixture", ordinal=2, stdin="kill\n"),
    )
    path.write_text(
        "".join(canonical_json(item.model_dump(mode="json")) + "\n" for item in candidates),
        encoding="utf-8",
    )


def test_collector_is_deterministic_and_accounts_for_every_scoped_row() -> None:
    source, manifest, exclusions = _fixture_collection()
    assert source.rows_opened == 1
    assert len(manifest.tasks) == 1
    assert len(manifest.tasks[0].wrong_programs) == 2
    assert exclusions[0]["reasons"] == ["source-not-codenet-origin"]
    assert len(manifest.tasks) + len(exclusions) == 2
    manifest.verify_digest()

    _, repeated, repeated_exclusions = _fixture_collection()
    assert repeated == manifest
    assert repeated_exclusions == exclusions


def test_public_bundle_contains_no_scorer_only_fields(tmp_path: Path) -> None:
    source, manifest, _ = _fixture_collection()
    bundle = tmp_path / "public.jsonl"
    build_public_bundle(manifest, {7: source.raw}, bundle)

    record = json.loads(bundle.read_text(encoding="utf-8"))
    forbidden = {
        "private_tests",
        "generated_tests",
        "solutions",
        "incorrect_solutions",
        "expected",
    }
    assert forbidden.isdisjoint(record)
    assert set(record["public_tests"]) == {"input", "output"}


def test_candidate_schema_rejects_expected_output_leakage(tmp_path: Path) -> None:
    source, manifest, _ = _fixture_collection()
    bundle = tmp_path / "public.jsonl"
    build_public_bundle(manifest, {7: source.raw}, bundle)
    task_id = manifest.tasks[0].task_id
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        canonical_json(
            {
                "task_id": task_id,
                "generator": "leaky",
                "ordinal": 1,
                "stdin": "x\n",
                "expected_output": "secret",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_output"):
        seal_candidates(
            manifest,
            bundle,
            candidates,
            tmp_path / "receipt.json",
            budget_per_task=1,
        )


def test_candidate_sealing_rejects_unequal_generator_budgets(tmp_path: Path) -> None:
    source, manifest, _ = _fixture_collection()
    bundle = tmp_path / "public.jsonl"
    build_public_bundle(manifest, {7: source.raw}, bundle)
    task_id = manifest.tasks[0].task_id
    candidates = tmp_path / "candidates.jsonl"
    records = (
        Candidate(task_id=task_id, generator="a", ordinal=1, stdin="a1\n"),
        Candidate(task_id=task_id, generator="a", ordinal=2, stdin="a2\n"),
        Candidate(task_id=task_id, generator="b", ordinal=1, stdin="b1\n"),
    )
    candidates.write_text(
        "".join(canonical_json(item.model_dump(mode="json")) + "\n" for item in records),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shared budget"):
        seal_candidates(
            manifest,
            bundle,
            candidates,
            tmp_path / "receipt.json",
            budget_per_task=2,
        )


class _Executor:
    runtime_identity = "fixture/no-host-execution"

    def execute(self, source: str, stdin: str, task) -> ProgramExecution:  # noqa: ANN001
        del task
        if source == "wrong-alpha" and stdin.strip() == "kill":
            return ProgramExecution(outcome="completed", stdout="wrong\n", wall_ms=2)
        return ProgramExecution(outcome="completed", stdout="ok\n", wall_ms=2)


def test_hidden_scorer_reports_recall_controls_budget_and_receipts(tmp_path: Path) -> None:
    source, manifest, _ = _fixture_collection()
    bundle = tmp_path / "public.jsonl"
    build_public_bundle(manifest, {7: source.raw}, bundle)
    candidates = tmp_path / "candidates.jsonl"
    _candidate_file(candidates, manifest.tasks[0].task_id)
    receipt = tmp_path / "receipt.json"
    seal_candidates(manifest, bundle, candidates, receipt, budget_per_task=2)

    report = score_candidates(
        manifest,
        bundle,
        candidates,
        receipt,
        {7: source.raw},
        _Executor(),
    )
    aggregate = report["aggregates"][0]
    assert aggregate["killed_wrong_programs"] == 1
    assert aggregate["defect_recall_percent"] == 50.0
    assert aggregate["mutation_score_percent"] == 50.0
    assert aggregate["false_kill_rate_percent"] == 0.0
    assert aggregate["queries_to_first_finding_median"] == 2.0
    assert aggregate["total_executions"] == 10
    assert report["runtime"] == "fixture/no-host-execution"
    assert report["tasks"][0]["queries_to_first_finding"] == 2


class _RowsMustStayClosed(dict[int, Mapping[str, Any]]):
    def __getitem__(self, key: int) -> Mapping[str, Any]:
        raise AssertionError(f"hidden row {key} opened before receipt verification")


def test_tampered_candidate_file_is_rejected_before_hidden_rows_open(tmp_path: Path) -> None:
    source, manifest, _ = _fixture_collection()
    bundle = tmp_path / "public.jsonl"
    build_public_bundle(manifest, {7: source.raw}, bundle)
    candidates = tmp_path / "candidates.jsonl"
    _candidate_file(candidates, manifest.tasks[0].task_id)
    receipt = tmp_path / "receipt.json"
    seal_candidates(manifest, bundle, candidates, receipt, budget_per_task=2)
    candidates.write_text(candidates.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after sealing"):
        score_candidates(
            manifest,
            bundle,
            candidates,
            receipt,
            _RowsMustStayClosed(),
            _Executor(),
        )


def test_committed_real_manifest_meets_the_frozen_size_gate() -> None:
    manifest = load_manifest(ROOT / "evaluations" / "real" / "selection_manifest.json")
    exclusions = (
        (ROOT / "evaluations" / "real" / "exclusions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert len(manifest.tasks) == 20
    assert sum(len(task.wrong_programs) for task in manifest.tasks) == 500
    assert sum(len(task.accepted_controls) for task in manifest.tasks) == 60
    assert manifest.second_review_status == "pending"
    assert len(exclusions) == 1690
    assert len(manifest.tasks) + len(exclusions) == 1710


def test_collector_cache_is_gitignored() -> None:
    assert CodeContestsSource.__doc__
    assert ".cache/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_unversioned_rows_api_is_blocked_when_dataset_head_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = CodeContestsSource(tmp_path)
    response = BytesIO(b'{"sha":"different-revision"}')
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response)

    with pytest.raises(ValueError, match="dataset HEAD changed"):
        source._verify_dataset_head()
