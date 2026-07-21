# Evaluation

## Current reproducible claim

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

These figures describe one deterministic demo assignment. They are not evidence of performance
across real courses, languages, or datasets.

## Frozen-evaluation policy

The expected result file is committed, but the hypothesis provider does not receive it. GPT-5.6
sees the assignment schema, instructor tests, and the descriptions of surviving misconceptions.
It does not receive expected outputs for candidate inputs, the minimized answer, or frozen
benchmark labels. Correctness comes from accepted-solution consensus and execution.

## Next benchmark gate

Before making a general impact claim, add a held-out, license-reviewed benchmark with:

- at least 20 assignments and 500 non-equivalent wrong solutions or mutants;
- hidden labels inaccessible to the hypothesis provider;
- baseline comparisons against public tests and deterministic generators;
- defect recall, mutation score, false-kill rate, abstention rate, latency, and cost;
- replayable inputs, program outputs, timeouts, and dataset provenance.

CodeContests, IntroClass, and Refactory are candidates, but none are vendored yet. Their terms,
task filters, and redistribution conditions must be verified before use.

