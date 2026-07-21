# Frozen evaluation corpus

`coursefuzz-synthetic-heldout-v1` contains ten independently named, bounded introductory Python
assignments, twenty accepted controls, and sixty executable wrong programs. The cases were authored
for CourseFuzz under this repository's Apache-2.0 license; they contain no student data or copied
course material.

The evaluation is held out at the provider boundary, not claimed as a real-course sample. A
hypothesis provider receives only assignment title, summary, input names, domain, existing input
tuples and labels, plus misconception descriptions for surviving programs. It cannot receive
program source, accepted controls, expected outputs, candidate witnesses, or frozen thresholds.
Execution and accepted-control consensus determine correctness after a hypothesis is proposed.

Run the benchmark from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/run_frozen_benchmark.py
```

The script runs inference before opening `frozen_expectations.json`, compares CourseFuzz with the
original instructor tests and a deterministic random-8 input baseline, verifies the corpus digest
and frozen metric floors, and writes `results/frozen-deterministic.json`.

This corpus proves repeatability and the safety mechanics of the repair loop. It does not prove
generalization to real courses, superiority over random search, student-learning impact, or a
GPT-5.6 advantage. Those require licensed external data and independent human review.
