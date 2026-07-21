# Real-corpus evaluation v1

This directory freezes a **non-vendored, provisional** CodeContests selection for CourseFuzz. It
contains 20 stdin/stdout Python 3 tasks, receipts for 40 oracle programs, 60 accepted holdout
controls, and 500 distinct wrong-program sources. It does not contain descriptions, tests, or
program source.

The selection receipt is `selection_manifest.json`. It pins the DeepMind/Hugging Face dataset
revision, the five inspected Parquet shards, upstream repository commits, every selected record and
program hash, and the deterministic selection SHA-256. `exclusions.jsonl` records why each of the
other 1,690 scoped rows was excluded. Together, the two artifacts account for all 1,710 rows in the
selection scope.

## Why this source and why no scraping

The official CodeContests export already provides revisioned Parquet files and a structured row
API, so Scrapling is not used. Scraping contest HTML would add unstable parsing, rate-limit, and
provenance risk without adding evidence.

Only source code `6` (`ATCODER`) or `7` (`AIZU`) is allowed because CodeContests identifies those
sources as inherited from Project CodeNet. The current frozen selection contains only `ATCODER`
records. CodeContests labels non-code material CC BY 4.0 and its code Apache-2.0, but its notice also
preserves possible upstream third-party terms. Therefore raw rows stay in `.cache/`, redistribution
is limited to hashes and aggregates, and `license_review_status` remains provisional until a second
reviewer signs it.

## Reproduce the selection

```powershell
python -m pip install -e '.[dev,evaluation]'
python scripts/collect_real_corpus.py --check
python scripts/verify_real_manifest.py
```

The collector scans metadata only with DuckDB, fetches only the 20 selected rows from the official
dataset API, and stores about 31 MB in `.cache/coursefuzz-evaluation/`. The first run takes roughly
two minutes on a typical connection; selected rows are reused afterward. No cache file should be
committed or uploaded as a CI artifact.

The row API itself has no revision parameter. Before a cache miss, the collector checks that the
dataset repository's current HEAD still equals the pinned revision and stops if it has moved; every
cached row is then locked by its own canonical SHA-256. It never silently combines pinned Parquet
metadata with rows from a newer export.

Selection filters are frozen as code in `evaluations/real_corpus.py`:

- pinned train split and first five pinned Parquet shards;
- CodeNet-origin source allowlist (`ATCODER`, `AIZU`);
- ordinary stdin/stdout invocation only;
- at least one public and five hidden tests;
- at least five Python 3 accepted programs and 25 Python 3 wrong programs;
- distinct, non-empty programs no larger than 128 KiB;
- deterministic SHA-256 ranking, two oracle programs, three holdout controls, and 25 wrong programs
  per task.

## Leakage boundary

Candidate generation and scoring are separate operations:

1. `python scripts/prepare_real_evaluation.py bundle` verifies cached row hashes and writes a
   gitignored JSONL bundle containing only descriptions, public tests, resource limits, and public
   provenance.
2. Each generator reads only that bundle and emits `Candidate` JSONL records. The schema has no
   expected-output field.
3. `python scripts/prepare_real_evaluation.py seal --candidates <path> --budget <n>` rejects unknown
   tasks, duplicate inputs, and unequal generator budgets, then hashes the final candidate file.
4. `score_candidates` verifies that receipt **before** opening a hidden cached row. It uses two
   accepted programs as an execution-backed consensus oracle and the other three accepted programs
   as false-kill controls.

The scorer reports defect recall and a 95% Wilson interval, false-kill rate and interval, abstention,
queries to first finding, executions, and execution wall time per generator. Model calls, tokens,
and dollar cost must be joined from the shared generator ledger when that phase lands; they are not
fabricated here.

## What is not yet a public claim

This manifest freezes upstream labels; it does not yet prove all 500 wrong programs are
runtime-compatible or behaviorally non-equivalent under CourseFuzz's pinned image. Public metrics
remain blocked until:

- the versioned stdin/stdout invocation adapter supplies the scorer's `StdinExecutor` under the
  isolated `runsc` execution plane;
- every selected program is replayed and exclusions caused by runtime drift are versioned;
- equal-budget public, random, boundary/property, CourseFuzz-no-model, and CourseFuzz-model files
  are sealed and scored;
- a second human reviewer signs the source/license decision, task filters, accepted controls, and
  central labels.

Until those gates pass, describe this as a frozen real-corpus **selection and scorer contract**, not
as a completed real-world benchmark or evidence that CourseFuzz beats random.
