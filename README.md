# CourseFuzz

**Find the wrong solution your autograder still accepts—then approve and verify one exact repair.**

CourseFuzz is an execution-backed assessment red team for instructors. Import a real bounded
Python assignment, accepted controls, misconception programs, tests, and a write destination.
GPT-5.6 proposes attack hypotheses; independent program executions decide what is true. The
product minimizes a real counterexample, shows the exact pytest patch, requires hash-bound
approval, writes locally or opens a draft GitHub pull request, reads it back, and reruns the
entire misconception corpus.

> Status: [public beta on Render](https://coursefuzz.onrender.com) plus a reproducible local
> vertical slice. The demo video and live GitHub write receipt are not yet published, so this
> repository should not be presented as submission-ready.

## The 90-second golden path

1. Import an assignment manifest or open the seeded triangle-classifier example.
2. Watch hypotheses get filtered by execution, not model confidence.
3. Inspect the minimized `(1, 2, 2)` counterexample: expected `isosceles`, observed `scalene`.
4. Review the exact generated pytest and approve its SHA-256-bound payload.
5. Apply the patch locally or to a run-specific GitHub branch and draft pull request.
6. CourseFuzz reads the destination back, reruns every program, and persists the audit receipt.

## Reproducible proof

- Before: **5/8 mutants killed (62.5%)**; three plausible wrong solutions receive full marks.
- After one approved test: **8/8 killed (100%)**.
- Safety control: **2/2 independently authored accepted solutions still pass (100%)**.
- Frozen synthetic v1: **10 assignments / 60 wrong programs / 20 accepted controls**.
- Aggregate mutation score: **53.3% -> 95.0% (+41.7 points)** with **0% false kills**. Each single
  repair is chosen to discriminate the most wrong programs at once (a feedback-directed selection),
  not merely the smallest counterexample.
- Honest baseline: an equal-budget frozen random-8 search also reaches **95.0%** on this small
  corpus, so the result proves the verified repair loop—not search superiority or real-course
  generalization. On domains this small a random sweep saturates, so the directed selector cannot be
  shown to beat it here; see `docs/NEXT_STEPS.md` ("Gap 3, measured").

## Quickstart

Prerequisites: Python 3.11+, Node.js 22+, and npm.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
Set-Location web
npm install
npm run build
Set-Location ..
.\.venv\Scripts\python -m uvicorn coursefuzz.main:app --reload
```

Open `http://127.0.0.1:8000`. No API key is required: the bounded deterministic provider is
the honest fallback. Set `OPENAI_API_KEY` to activate GPT-5.6 hypothesis generation; the model
still never receives expected outputs and never decides correctness.

For GitHub delivery, set `COURSEFUZZ_GITHUB_TOKEN` to a fine-grained token with repository
`Contents: write` and `Pull requests: write`. The token stays in the environment and is never
stored in an assignment or run document.

For a shared deployment, set `COURSEFUZZ_ACCESS_KEYS_JSON` to a JSON object that maps tenant IDs
to distinct random tokens of at least 24 characters. Protected routes accept bearer credentials;
the browser exchanges the key for an eight-hour HttpOnly, SameSite-strict session cookie. Imported
assignments, runs, approvals, artifacts, and event streams are tenant-scoped. With no key map, the
health endpoint explicitly reports `local-demo` authentication mode.

## Architecture and trust boundary

```text
React proof sheet -> typed FastAPI route -> RunService -> AssessmentEngine
       |                  |              |-> GPT-5.6/fallback hypotheses
       |                  |              `-> restricted executions + oracle
       |                  |-> SQLite/Postgres snapshots, runs, approvals, audit, artifacts
       |                  `-> local artifact or GitHub draft PR + read-back
       `-> JSON manifest import + multi-assignment switcher
```

Approval is required before the only consequential write. The token is bound to the exact
payload hash. A run reaches `verified` only after destination read-back and a complete regression
rerun match the approved projection. The default Python runner is deliberately limited and is
**not** a production hostile-code sandbox; a no-network container backend
(`DockerIsolatedRunner`/`GVisorDockerRunner`, `--network none`, dropped capabilities, read-only
root) implements the same execution gateway and runs on a separate worker
(`python -m coursefuzz.worker`, `COURSEFUZZ_EXECUTION_BACKEND=gvisor`), with its gVisor `runsc`
path exercised live in CI. It is defense-in-depth wiring, not yet the default analysis path.

## Verify locally

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python scripts/run_frozen_benchmark.py --no-write
.\.venv\Scripts\python scripts/release_guard.py
Set-Location web
npm run build
```

`scripts/release_guard.py --submission` intentionally fails until the public app, video, and live
GitHub receipt are recorded in `release_manifest.json`; missing proof cannot be silently shipped.

The container serves the compiled frontend and API as one process:

```powershell
docker build -t coursefuzz .
docker run --rm -p 8000:8000 coursefuzz
```

## Reviewer map

- [Architecture](docs/ARCHITECTURE.md) — boundaries, state machine, and artifact closure
- [Product specification](docs/PRODUCT_SPEC.md) — supported contract and completion gates
- [Evaluation](docs/EVALUATION.md) — reproducible claim and frozen-evaluation policy
- [Security](docs/SECURITY.md) — implemented controls and deliberate limitations
- [Deployment](docs/DEPLOYMENT.md) — canonical container contract and clean smoke gate
- [Edge-case matrix](docs/EDGE_CASE_MATRIX.md) — covered, bounded, and release-blocking cases
- [Demo runbook](docs/DEMO_RUNBOOK.md) — the 2:50–2:55 recording script
- [Deepening plan](docs/NEXT_STEPS.md) — gated roadmap from public proof to safe real-course use
- [Design context](.impeccable.md) — audience, brand, visual direction, and accessibility rules

## Current limitations

Opaque-key authentication and tenant isolation are implemented for the single-instance slice;
there is no institutional identity provider, LMS ingestion, PII pipeline, or held-out cross-course
benchmark yet. A no-network container backend (gVisor `runsc`) implements the execution gateway and
is exercised in CI, but the restricted local runner is still the default analysis path and running
genuinely untrusted code additionally needs seccomp/user-namespace policy and image provenance.
GitHub delivery is implemented and contract-tested
with a deterministic fake transport, but still needs logged-out proof against a dedicated live
repository. Hosted Postgres is single-instance demo persistence, and its free
Render instance expires after 30 days without backups. Synthetic and fallback behavior is visibly
labelled.

Apache-2.0 licensed. See [LICENSE](LICENSE).
