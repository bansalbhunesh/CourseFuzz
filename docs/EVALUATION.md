# Evaluation

## Current reproducible claims

For the seeded triangle-classification assignment:

- Instructor tests kill 5 of 8 realistic misconception mutants: **62.5% mutation score**.
- Three wrong solutions still receive full marks.
- CourseFuzz verifies and minimizes the input to `(1, 2, 2)`.
- The approved regression kills all 8 mutants: **100% mutation score**.
- Both independently authored accepted solutions continue to pass: **100% accepted-solution pass rate**.

Run the evidence locally:

```powershell
.\.venv\Scripts\python -m pytest tests/test_engine.py
.\.venv\Scripts\python scripts/run_demo_benchmark.py
```

For frozen synthetic benchmark v1:

- 10 bounded assignments, 60 executable wrong programs, and 20 accepted controls;
- instructor suites kill 32/60 wrong programs: **53.3% mutation score**;
- one CourseFuzz repair per assignment kills 56/60: **93.3% (+40.0 points)**;
- all 20 accepted controls still pass: **0% false-kill rate**;
- findings are produced for 10/10 assignments with no abstentions;
- a frozen equal-budget random-8 provider also reaches **93.3%**.

The random tie is a material limitation. This benchmark supports the claim that the complete
verification-and-repair loop improves these instructor suites without rejecting accepted controls.
It does not support a claim that deterministic CourseFuzz search beats random input generation.

Run and verify the frozen evidence:

```powershell
.\.venv\Scripts\python scripts/run_frozen_benchmark.py
```

The committed result is `evaluations/results/frozen-deterministic.json`; its corpus SHA-256 is
locked in `evaluations/frozen_expectations.json` and checked in CI.

## Frozen-evaluation policy

The runner completes all inference before opening the expected-result file. Every hypothesis
provider receives a sanitized `HypothesisContext`: title, summary, input names, bounded domain,
existing input tuples and labels, and source-free survivor hints. The type contains no program
source, accepted controls, expected outputs, minimized answer, or frozen labels. Correctness comes
from accepted-solution consensus and execution after proposals cross that boundary.

Synthetic v1 was authored within this repository, contains no personal data, and is not presented
as a real-course or human-reviewed sample. See `evaluations/README.md` for provenance and limits.

## Next benchmark gate

Before making a general educational-impact or search-superiority claim, add a license-reviewed
external benchmark with:

- at least 20 assignments and 500 non-equivalent wrong solutions or mutants;
- hidden labels inaccessible to the hypothesis provider;
- baseline comparisons against public tests and deterministic generators;
- defect recall, mutation score, false-kill rate, abstention rate, latency, and cost;
- replayable inputs, program outputs, timeouts, and dataset provenance.

CodeContests, IntroClass, and Refactory remain candidates, but none are vendored. Their terms,
task filters, redistribution conditions, and label quality must be verified before use. A second
human reviewer must sign off the central labels before the public claim is upgraded.
