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

## External-corpus decision record

- **CodeContests — adopt for a non-vendored next evaluation.** The official archived repository
  provides train/validation/test splits, paired tests, and correct and incorrect human solutions.
  Its code is Apache-2.0 and non-code material is CC BY 4.0, while its notice explicitly warns that
  upstream third-party terms can still apply. The full corpus is about 3 GiB in Riegeli format and
  its supported harness is Linux/Bazel, so CourseFuzz should commit only selection manifests,
  source URLs, hashes, attribution, and derived aggregate results—not copied submissions.
  Source: [Google DeepMind CodeContests](https://github.com/google-deepmind/code_contests).
- **IntroClass — legally clear, product-language mismatch.** The benchmark is BSD-licensed real
  introductory-course work with defects and test suites, but all six subject programs are C. It is
  valuable corroborating evidence only after CourseFuzz has a real C execution adapter; adding C
  solely to inflate the benchmark would distort the shipped Python product.
  Source: [ManyBugs and IntroClass](https://repairbenchmarks.cs.umass.edu/).
- **Project CodeNet — reject as the primary oracle corpus.** It has roughly 14 million submissions,
  rich status metadata, and substantial Python coverage, but the public package generally exposes
  problem descriptions and sample input/output rather than the online judges' full hidden test
  suites. Accepted/wrong labels are useful for sampling, but they do not independently reproduce
  expected outputs for CourseFuzz's counterexamples.
  Source: [IBM Project CodeNet](https://github.com/IBM/Project_CodeNet).
- **Refactory — do not redistribute without permission.** The paper reports almost 1,800 real
  incorrect Python submissions from 361 students, making it the closest conceptual match, but the
  public record located for this audit provides the paper—not an explicit reusable student-corpus
  license. CourseFuzz must obtain author/institution permission before using those submissions.
  Source: [UCL Refactory publication record](https://discovery.ucl.ac.uk/id/eprint/10091878/).
