# CourseFuzz

**Find the wrong solution your autograder still accepts—then approve and verify one exact repair.**

CourseFuzz is an execution-backed assessment red team for instructors. GPT-5.6 proposes
bounded attack hypotheses; independent program executions decide what is true. The product
minimizes a real counterexample, shows the exact pytest patch, requires hash-bound approval,
writes the artifact, reads it back, and reruns the entire mutant corpus.

> Status: reproducible local vertical slice. The public deployment and demo video are not yet
> published, so this repository should not be presented as submission-ready.

## The 90-second golden path

1. Open the seeded triangle-classifier assignment and start a red-team run.
2. Watch hypotheses get filtered by execution, not model confidence.
3. Inspect the minimized `(1, 2, 2)` counterexample: expected `isosceles`, observed `scalene`.
4. Review the exact generated pytest and approve its SHA-256-bound payload.
5. Apply the patch; CourseFuzz reads the file back and reruns all mutants and accepted controls.
6. Download the verified artifact and inspect the persisted audit trail.

## Reproducible proof

- Before: **5/8 mutants killed (62.5%)**; three plausible wrong solutions receive full marks.
- After one approved test: **8/8 killed (100%)**.
- Safety control: **2/2 independently authored accepted solutions still pass (100%)**.
- Scope: one deterministic seeded Python assignment; no cross-course performance claim.

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

## Architecture and trust boundary

```text
React proof sheet -> typed FastAPI route -> RunService -> AssessmentEngine
                                               |              |-> GPT-5.6/fallback hypotheses
                                               |              `-> restricted executions + oracle
                                               `-> SQLite audit + hash-bound artifact repository
```

Approval is required before the only consequential write. The token is bound to the exact
payload hash. A run reaches `verified` only after destination read-back and a complete regression
rerun match the approved projection. The Python runner is deliberately limited and is **not** a
production hostile-code sandbox.

## Verify locally

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
Set-Location web
npm run build
```

The container serves the compiled frontend and API as one process:

```powershell
docker build -t coursefuzz .
docker run --rm -p 8000:8000 coursefuzz
```

## Reviewer map

- [Architecture](docs/ARCHITECTURE.md) — boundaries, state machine, and artifact closure
- [Evaluation](docs/EVALUATION.md) — reproducible claim and frozen-evaluation policy
- [Security](docs/SECURITY.md) — implemented controls and deliberate limitations
- [Edge-case matrix](docs/EDGE_CASE_MATRIX.md) — covered, bounded, and release-blocking cases
- [Demo runbook](docs/DEMO_RUNBOOK.md) — the 2:50–2:55 recording script
- [Design context](.impeccable.md) — audience, brand, visual direction, and accessibility rules

## Current limitations

There is no authentication, tenant isolation, LMS ingestion, PII pipeline, hardened multi-tenant
sandbox, public deployment, or held-out cross-course benchmark yet. SQLite supports a durable
single-instance demo, not horizontal scale. Synthetic and fallback behavior is visibly labelled.

Apache-2.0 licensed. See [LICENSE](LICENSE).
