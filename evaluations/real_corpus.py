"""Pinned, non-vendored acquisition for the CourseFuzz real-corpus evaluation.

The collector deliberately keeps raw problem descriptions, tests, and programs in a gitignored
cache.  Git receives only provenance, content hashes, selection decisions, and exclusion reasons.
The upstream row API is used only after a metadata-only DuckDB scan has deterministically selected
records, avoiding a multi-gigabyte download and avoiding HTML scraping.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
DATASET_ID = "deepmind/code_contests"
DATASET_REVISION = "802411c3010cb00d1b05bad57ca77365a3c699d6"
CODE_CONTESTS_COMMIT = "fa7a4f8139aab08362503f3344778eb86901709a"
CODENET_COMMIT = "55c323b527ab3d6510d55edff188bc0a0d7bc5e5"
ROWS_ENDPOINT = "https://datasets-server.huggingface.co/rows"
DATASET_API = f"https://huggingface.co/api/datasets/{DATASET_ID}"
PARQUET_BASE = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{DATASET_REVISION}/data"

# The first five pinned train shards contain enough eligible CodeNet-origin Python 3 records for
# the v1 gate. Restricting the scope keeps regeneration bounded and makes every exclusion auditable.
PINNED_SHARDS = (
    "train-00000-of-00039-e991a271dbfa9925.parquet",
    "train-00001-of-00039-e092fe56fda18715.parquet",
    "train-00002-of-00039-9cea23812e920e41.parquet",
    "train-00003-of-00039-e3822fccad6e083a.parquet",
    "train-00004-of-00039-cefe355b4667b27e.parquet",
)

PYTHON3 = 3
ATCODER = 6
AIZU = 7
SOURCE_NAMES = {ATCODER: "ATCODER", AIZU: "AIZU"}


def canonical_json(value: Any) -> str:
    """Return the one JSON representation used for every provenance receipt."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_source(value: str) -> str:
    """Normalize transport-only newline differences for duplicate detection."""

    return value.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


class ProgramReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream_index: int = Field(ge=0)
    language: str = "python3"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(gt=0)


class TaskReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    upstream_id: str
    upstream_name: str
    split: str = "train"
    shard: str
    global_row_index: int = Field(ge=0)
    source: str
    public_test_count: int = Field(ge=1)
    hidden_test_count: int = Field(ge=1)
    raw_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hidden_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    oracle_programs: tuple[ProgramReceipt, ProgramReceipt]
    accepted_controls: tuple[ProgramReceipt, ...] = Field(min_length=1)
    wrong_programs: tuple[ProgramReceipt, ...] = Field(min_length=1)
    upstream_python3_correct: int = Field(ge=1)
    upstream_python3_wrong: int = Field(ge=1)
    label_validation_status: str = "upstream-label-only"

    @model_validator(mode="after")
    def program_roles_are_disjoint(self) -> TaskReceipt:
        accepted = {item.normalized_sha256 for item in self.oracle_programs}
        controls = {item.normalized_sha256 for item in self.accepted_controls}
        wrong = {item.normalized_sha256 for item in self.wrong_programs}
        if len(accepted) != len(self.oracle_programs):
            raise ValueError("oracle programs must be distinct")
        if accepted & controls or accepted & wrong or controls & wrong:
            raise ValueError("oracle, accepted-control, and wrong-program roles must be disjoint")
        return self


class CorpusManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    corpus_id: str = "coursefuzz-codecontests-codenet-python3-v1"
    dataset_id: str = DATASET_ID
    dataset_revision: str = DATASET_REVISION
    dataset_repository: str = "https://github.com/google-deepmind/code_contests"
    dataset_repository_commit: str = CODE_CONTESTS_COMMIT
    upstream_repository: str = "https://github.com/IBM/Project_CodeNet"
    upstream_repository_commit: str = CODENET_COMMIT
    dataset_license: str = "CC-BY-4.0"
    upstream_repository_license: str = "Apache-2.0"
    redistribution_policy: str = "hashes-and-derived-aggregates-only"
    license_review_status: str = "provisional-second-review-required"
    split: str = "train"
    pinned_shards: tuple[str, ...] = PINNED_SHARDS
    selection_seed: str = "coursefuzz-real-v1"
    task_target: int = 20
    wrong_programs_per_task: int = 25
    oracle_programs_per_task: int = 2
    accepted_controls_per_task: int = 3
    tasks: tuple[TaskReceipt, ...]
    second_reviewer: str | None = None
    second_review_status: str = "pending"
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def gate_counts_match(self) -> CorpusManifest:
        if len(self.tasks) != self.task_target:
            raise ValueError(f"manifest requires exactly {self.task_target} tasks")
        expected_wrong = self.task_target * self.wrong_programs_per_task
        if sum(len(task.wrong_programs) for task in self.tasks) != expected_wrong:
            raise ValueError(f"manifest requires exactly {expected_wrong} wrong programs")
        if any(
            len(task.accepted_controls) != self.accepted_controls_per_task for task in self.tasks
        ):
            raise ValueError("accepted-control count differs from the frozen policy")
        if len({task.task_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("task IDs must be unique")
        wrong_hashes = [
            program.normalized_sha256 for task in self.tasks for program in task.wrong_programs
        ]
        accepted_hashes = [
            program.normalized_sha256
            for task in self.tasks
            for program in (*task.oracle_programs, *task.accepted_controls)
        ]
        if len(set(wrong_hashes)) != len(wrong_hashes):
            raise ValueError("wrong-program sources must be globally distinct")
        if len(set(accepted_hashes)) != len(accepted_hashes):
            raise ValueError("accepted-program sources must be globally distinct")
        if set(wrong_hashes) & set(accepted_hashes):
            raise ValueError("accepted and wrong source receipts must not overlap")
        return self

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"selection_sha256"})

    def verify_digest(self) -> None:
        observed = sha256_json(self.digest_payload())
        if observed != self.selection_sha256:
            raise ValueError(
                f"selection SHA-256 mismatch: manifest={self.selection_sha256}, observed={observed}"
            )


@dataclass(frozen=True)
class SelectionPolicy:
    allowed_sources: tuple[int, ...] = (ATCODER, AIZU)
    minimum_public_tests: int = 1
    minimum_hidden_tests: int = 5
    minimum_python3_correct: int = 5
    minimum_python3_wrong: int = 25
    maximum_source_bytes: int = 131_072
    tasks: int = 20
    oracle_programs: int = 2
    accepted_controls: int = 3
    wrong_programs: int = 25
    seed: str = "coursefuzz-real-v1"


@dataclass(frozen=True)
class MetadataRecord:
    name: str
    source: int
    shard: str
    global_row_index: int
    public_tests: int
    private_tests: int
    generated_tests: int
    python3_correct: int
    python3_wrong: int
    input_file: str
    output_file: str


class CorpusSource(Protocol):
    def metadata(self) -> tuple[MetadataRecord, ...]: ...

    def row(self, global_row_index: int) -> Mapping[str, Any]: ...


class CodeContestsSource:
    """Read pinned metadata with DuckDB and selected rows with the official viewer API."""

    def __init__(self, cache_dir: Path, *, timeout_seconds: float = 120.0) -> None:
        self.cache_dir = cache_dir
        self.timeout_seconds = timeout_seconds
        self._head_verified = False

    def metadata(self) -> tuple[MetadataRecord, ...]:
        try:
            import duckdb
        except ImportError as error:  # pragma: no cover - exercised by the CLI environment
            raise RuntimeError(
                "Install CourseFuzz with the evaluation extra: pip install -e .[evaluation]"
            ) from error

        connection = duckdb.connect()
        records: list[MetadataRecord] = []
        global_offset = 0
        query = """
            SELECT
                file_row_number,
                name,
                source,
                list_count(public_tests.input),
                list_count(private_tests.input),
                list_count(generated_tests.input),
                list_count(list_filter(solutions.language, item -> item = 3)),
                list_count(list_filter(incorrect_solutions.language, item -> item = 3)),
                coalesce(input_file, ''),
                coalesce(output_file, '')
            FROM read_parquet(?, file_row_number=true)
            ORDER BY file_row_number
        """
        try:
            for shard in PINNED_SHARDS:
                url = f"{PARQUET_BASE}/{shard}"
                rows = connection.execute(query, [url]).fetchall()
                for row in rows:
                    records.append(
                        MetadataRecord(
                            name=str(row[1]),
                            source=int(row[2]),
                            shard=shard,
                            global_row_index=global_offset + int(row[0]),
                            public_tests=int(row[3]),
                            private_tests=int(row[4]),
                            generated_tests=int(row[5]),
                            python3_correct=int(row[6]),
                            python3_wrong=int(row[7]),
                            input_file=str(row[8]),
                            output_file=str(row[9]),
                        )
                    )
                global_offset += len(rows)
        finally:
            connection.close()
        return tuple(records)

    def row(self, global_row_index: int) -> Mapping[str, Any]:
        path = self.cache_dir / DATASET_REVISION / f"train-row-{global_row_index:05d}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

        self._verify_dataset_head()

        params = urllib.parse.urlencode(
            {
                "dataset": DATASET_ID,
                "config": "default",
                "split": "train",
                "offset": global_row_index,
                "length": 1,
            }
        )
        request = urllib.request.Request(
            f"{ROWS_ENDPOINT}?{params}",
            headers={"User-Agent": "CourseFuzz/0.1 real-corpus-collector"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            payload = json.load(response)
        rows = payload.get("rows", [])
        if len(rows) != 1 or int(rows[0].get("row_idx", -1)) != global_row_index:
            raise ValueError(f"dataset API returned the wrong row for offset {global_row_index}")
        if rows[0].get("truncated_cells"):
            raise ValueError(f"dataset API truncated row {global_row_index}")
        row = rows[0]["row"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(row) + "\n", encoding="utf-8")
        return row

    def _verify_dataset_head(self) -> None:
        """Rows API has no revision argument, so refuse it unless HEAD is the pinned revision."""

        if self._head_verified:
            return
        request = urllib.request.Request(
            DATASET_API,
            headers={"User-Agent": "CourseFuzz/0.1 real-corpus-collector"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            metadata = json.load(response)
        observed = metadata.get("sha")
        if observed != DATASET_REVISION:
            raise ValueError(
                "dataset HEAD changed; the unversioned rows API cannot reproduce the pinned "
                f"revision (expected {DATASET_REVISION}, observed {observed})"
            )
        self._head_verified = True


def metadata_exclusion_reasons(item: MetadataRecord, policy: SelectionPolicy) -> tuple[str, ...]:
    reasons: list[str] = []
    if item.source not in policy.allowed_sources:
        reasons.append("source-not-codenet-origin")
    if item.public_tests < policy.minimum_public_tests:
        reasons.append("insufficient-public-tests")
    if item.private_tests + item.generated_tests < policy.minimum_hidden_tests:
        reasons.append("insufficient-hidden-tests")
    if item.python3_correct < policy.minimum_python3_correct:
        reasons.append("insufficient-python3-accepted-programs")
    if item.python3_wrong < policy.minimum_python3_wrong:
        reasons.append("insufficient-python3-wrong-programs")
    if item.input_file or item.output_file:
        reasons.append("non-stdio-invocation")
    return tuple(reasons)


def _program_receipts(
    programs: Mapping[str, Sequence[Any]], policy: SelectionPolicy
) -> tuple[ProgramReceipt, ...]:
    languages = tuple(programs.get("language", ()))
    sources = tuple(programs.get("solution", ()))
    if len(languages) != len(sources):
        raise ValueError("upstream solution language/source arrays are misaligned")
    unique: dict[str, ProgramReceipt] = {}
    for index, (language, source_value) in enumerate(zip(languages, sources, strict=True)):
        if (
            int(language) != PYTHON3
            or not isinstance(source_value, str)
            or not source_value.strip()
        ):
            continue
        source_bytes = len(source_value.encode("utf-8"))
        if source_bytes > policy.maximum_source_bytes:
            continue
        normalized_sha = sha256_text(normalized_source(source_value))
        receipt = ProgramReceipt(
            upstream_index=index,
            source_sha256=sha256_text(source_value),
            normalized_sha256=normalized_sha,
            source_bytes=source_bytes,
        )
        previous = unique.get(normalized_sha)
        if previous is None or receipt.upstream_index < previous.upstream_index:
            unique[normalized_sha] = receipt
    return tuple(
        sorted(unique.values(), key=lambda item: (item.normalized_sha256, item.upstream_index))
    )


def _upstream_id(name: str) -> str:
    match = re.match(r"^(p\d{5})\b", name, flags=re.IGNORECASE)
    return match.group(1).lower() if match else f"name-{sha256_text(name)[:12]}"


def _selection_key(item: MetadataRecord, seed: str) -> str:
    return sha256_text(f"{seed}\0train\0{item.shard}\0{item.global_row_index}\0{item.name}")


def _validate_row_matches_metadata(row: Mapping[str, Any], metadata: MetadataRecord) -> None:
    if row.get("name") != metadata.name or int(row.get("source", -1)) != metadata.source:
        raise ValueError(f"row {metadata.global_row_index} does not match its metadata receipt")


def _task_receipt(
    row: Mapping[str, Any], metadata: MetadataRecord, policy: SelectionPolicy
) -> tuple[TaskReceipt | None, tuple[str, ...]]:
    _validate_row_matches_metadata(row, metadata)
    correct = _program_receipts(row["solutions"], policy)
    wrong = _program_receipts(row["incorrect_solutions"], policy)
    accepted_needed = policy.oracle_programs + policy.accepted_controls
    reasons: list[str] = []
    if len(correct) < accepted_needed:
        reasons.append("insufficient-distinct-bounded-accepted-programs")
    if len(wrong) < policy.wrong_programs:
        reasons.append("insufficient-distinct-bounded-wrong-programs")
    if {item.normalized_sha256 for item in correct} & {item.normalized_sha256 for item in wrong}:
        reasons.append("accepted-wrong-source-overlap")
    if reasons:
        return None, tuple(reasons)

    public_context = {
        "name": row["name"],
        "description": row["description"],
        "public_tests": row["public_tests"],
        "source": row["source"],
        "difficulty": row["difficulty"],
        "time_limit": row["time_limit"],
        "memory_limit_bytes": row["memory_limit_bytes"],
    }
    chosen_correct = correct[:accepted_needed]
    chosen_wrong = wrong[: policy.wrong_programs]
    hidden_evidence = {
        "private_tests": row["private_tests"],
        "generated_tests": row["generated_tests"],
        "oracle_program_sha256": [item.source_sha256 for item in chosen_correct[:2]],
        "control_program_sha256": [item.source_sha256 for item in chosen_correct[2:]],
        "wrong_program_sha256": [item.source_sha256 for item in chosen_wrong],
    }
    upstream_id = _upstream_id(metadata.name)
    task_id = f"cc-train-{metadata.global_row_index:05d}-{upstream_id}"
    return (
        TaskReceipt(
            task_id=task_id,
            upstream_id=upstream_id,
            upstream_name=metadata.name,
            shard=metadata.shard,
            global_row_index=metadata.global_row_index,
            source=SOURCE_NAMES[metadata.source],
            public_test_count=metadata.public_tests,
            hidden_test_count=metadata.private_tests + metadata.generated_tests,
            raw_record_sha256=sha256_json(row),
            public_context_sha256=sha256_json(public_context),
            hidden_evidence_sha256=sha256_json(hidden_evidence),
            oracle_programs=(chosen_correct[0], chosen_correct[1]),
            accepted_controls=chosen_correct[2:],
            wrong_programs=chosen_wrong,
            upstream_python3_correct=metadata.python3_correct,
            upstream_python3_wrong=metadata.python3_wrong,
        ),
        (),
    )


def collect_manifest(
    source: CorpusSource, policy: SelectionPolicy | None = None
) -> tuple[CorpusManifest, tuple[dict[str, Any], ...]]:
    """Collect exactly the frozen gate and return a complete exclusion ledger."""

    policy = policy or SelectionPolicy()
    metadata = source.metadata()
    selected: list[TaskReceipt] = []
    exclusions_by_row: dict[int, dict[str, Any]] = {}
    eligible: list[MetadataRecord] = []
    for item in metadata:
        reasons = metadata_exclusion_reasons(item, policy)
        if reasons:
            exclusions_by_row[item.global_row_index] = {
                "global_row_index": item.global_row_index,
                "name": item.name,
                "shard": item.shard,
                "reasons": list(reasons),
            }
        else:
            eligible.append(item)

    ranked = sorted(eligible, key=lambda item: (_selection_key(item, policy.seed), item.name))
    for item in ranked:
        if len(selected) >= policy.tasks:
            exclusions_by_row[item.global_row_index] = {
                "global_row_index": item.global_row_index,
                "name": item.name,
                "shard": item.shard,
                "reasons": ["selection-limit"],
            }
            continue
        receipt, reasons = _task_receipt(source.row(item.global_row_index), item, policy)
        if receipt is None:
            exclusions_by_row[item.global_row_index] = {
                "global_row_index": item.global_row_index,
                "name": item.name,
                "shard": item.shard,
                "reasons": list(reasons),
            }
        else:
            selected.append(receipt)

    if len(selected) != policy.tasks:
        raise ValueError(f"selection produced {len(selected)} tasks; expected {policy.tasks}")

    selected.sort(key=lambda item: item.task_id)
    draft = CorpusManifest.model_construct(
        tasks=tuple(selected),
        task_target=policy.tasks,
        wrong_programs_per_task=policy.wrong_programs,
        oracle_programs_per_task=policy.oracle_programs,
        accepted_controls_per_task=policy.accepted_controls,
        selection_seed=policy.seed,
        selection_sha256="0" * 64,
    )
    digest = sha256_json(draft.digest_payload())
    manifest = CorpusManifest.model_validate(
        {**draft.model_dump(mode="json"), "selection_sha256": digest}
    )
    manifest.verify_digest()
    exclusions = tuple(exclusions_by_row[index] for index in sorted(exclusions_by_row))
    if len(exclusions) + len(selected) != len(metadata):
        raise ValueError("exclusion ledger does not account for every scoped upstream row")
    return manifest, exclusions


def load_manifest(path: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))
    manifest.verify_digest()
    return manifest


def write_collection(
    manifest: CorpusManifest,
    exclusions: Iterable[Mapping[str, Any]],
    manifest_path: Path,
    exclusions_path: Path,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    exclusions_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    exclusions_path.write_text(
        "".join(canonical_json(item) + "\n" for item in exclusions), encoding="utf-8"
    )


def verify_cached_row(task: TaskReceipt, row: Mapping[str, Any]) -> None:
    if sha256_json(row) != task.raw_record_sha256:
        raise ValueError(f"{task.task_id}: cached raw row does not match its frozen SHA-256")


def selected_source(row: Mapping[str, Any], role: str, receipt: ProgramReceipt) -> str:
    field = "incorrect_solutions" if role == "wrong" else "solutions"
    sources = row[field]["solution"]
    languages = row[field]["language"]
    try:
        source = sources[receipt.upstream_index]
        language = int(languages[receipt.upstream_index])
    except (IndexError, TypeError) as error:
        raise ValueError(f"selected {role} program is missing from the cached row") from error
    if language != PYTHON3 or sha256_text(source) != receipt.source_sha256:
        raise ValueError(f"selected {role} program does not match its frozen receipt")
    return source
