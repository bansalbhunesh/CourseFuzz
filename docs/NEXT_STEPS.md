# CourseFuzz deepening plan

This is the canonical plan for expanding CourseFuzz after the verified vertical slice. The order
is deliberate: deepen the trust boundary, evidence, and central algorithm before adding product
breadth. A milestone starts only when the previous milestone's exit gate is satisfied.

## North-star outcome

An instructor can import a real Python assignment and anonymized submissions, discover a grading
gap that ordinary tests and equal-budget random search miss, approve one exact repair, deliver it
to the real autograder repository, and receive execution-backed proof that the repair catches the
wrong submissions without rejecting independently accepted solutions.

The product is deeper when each claim has a durable artifact and an adversarial test—not when the
repository has more pages, agents, or integrations.

## Current baseline

CourseFuzz already has the full local workflow: immutable assignment snapshots, bounded hypothesis
generation, independent oracle execution, counterexample minimization, exact-payload approval,
local and GitHub destinations, destination read-back, regression verification, tenant isolation,
recovery, audit events, a responsive UI, CI, and a frozen synthetic benchmark.

The next work must close three honest gaps:

1. The restricted subprocess is a demo boundary, not hostile-code isolation.
2. Synthetic benchmark v1 is not evidence of real-course generalization.
3. Deterministic CourseFuzz ties an equal-budget random-8 baseline, so search superiority is not
   established.

## Milestone 0 — finish the public proof loop

Purpose: turn the existing implementation into judge-verifiable evidence before changing the core.

Work:

- Push `agent/actual-product`, review it through a draft pull request, and merge only after CI.
- Create a dedicated GitHub demo-target repository; never use the product repository as the write
  target.
- Deploy the existing `render.yaml` shape with a persistent volume, required authentication, the
  single-repository GitHub allowlist, and immutable commit evidence.
- Run the complete deployed flow from a clean logged-out desktop and phone-sized browser.
- Record the 2:50–2:55 demo with burned and platform captions. Show a live draft pull request and
  its read-back receipt.
- Add the public app, video, repository, and live receipt URLs to `release_manifest.json`.

Exit gate:

- `python scripts/release_guard.py --submission` passes.
- CI is green on the deployed commit.
- Restarting the hosted service preserves assignment, run, approval, and audit history.
- The video remains understandable muted and exposes the honest random-baseline limitation.

Do not begin a UI redesign or add another language during this milestone.

## Milestone 1 — replace the demo runner with an execution plane

Purpose: make the central safety boundary credible for untrusted student programs.

Code shape:

```text
AssessmentEngine
  -> ExecutionGateway (domain protocol)
       -> LocalRestrictedRunner (development only)
       -> RemoteIsolatedRunner (production)
            -> one ephemeral sandbox per execution batch
```

Introduce versioned `ExecutionRequest` and `ExecutionResult` contracts containing assignment
snapshot SHA, source SHA, entrypoint, typed inputs, language/runtime version, CPU and wall limits,
memory/PID/output ceilings, outcome, and receipt. Keep provider prompts and workflow services
unaware of the concrete sandbox vendor.

Production runner requirements:

- no network and no inherited cloud credentials;
- read-only base filesystem and a fresh bounded scratch directory;
- unprivileged per-run identity, process-group termination, CPU/memory/PID quotas, and a total
  deadline enforced outside the guest;
- source and output size limits before persistence;
- signed or authenticated runner requests and receipts;
- bounded concurrency, queue backpressure, cancellation, and idempotent replay;
- runtime image pinned by digest and returned in every receipt.

Tests:

- adapter contract suite shared by local and remote runners;
- abuse corpus covering fork/process bombs, infinite loops, memory growth, large output, filesystem
  traversal, environment reads, socket attempts, signals, syntax errors, and interpreter crashes;
- crash-before-receipt and duplicate-delivery integration tests;
- a clean deployed smoke test proving network denial and quota enforcement.

Exit gate:

- No untrusted program executes inside the API container.
- Every execution has a persisted, runtime-pinned receipt.
- The abuse corpus passes without depending on source-AST restrictions for containment.

## Milestone 2 — strengthen the oracle and assignment contract

Purpose: avoid treating two accepted programs that share a bug as ground truth.

Refactor correctness into explicit oracle adapters:

- `ConsensusOracle`: current independently authored accepted-solution agreement.
- `ReferenceOracle`: instructor-owned executable reference with recorded provenance.
- `PropertyOracle`: deterministic invariants and metamorphic relations.
- `FixtureOracle`: reviewed input/output cases imported from the source course.

Add an `OracleDecision` value with expected output, evidence sources, quorum, confidence policy,
and an abstention reason. The engine must abstain on disagreement, nondeterminism, timeout, or
insufficient provenance; model confidence never upgrades an oracle result.

Extend the versioned assignment manifest with explicit oracle mode, provenance, runtime, input and
output schemas, invocation mode (`python_callable` or `stdin_program`), and deterministic
serialization. Put invocation behind an adapter so the engine consumes typed cases without
hard-coding either function arguments or contest stdin formatting. Add migrations rather than
silently changing the meaning of stored snapshots.

Tests and exit gate:

- shared-bug accepted controls produce an abstention;
- nondeterministic and flaky programs are detected by repeated execution;
- properties are checked before a patch can reach approval;
- historical v1 snapshots replay identically after schema migration;
- callable and stdin/stdout fixtures pass the same execution and oracle contract tests;
- every expected value displayed in the UI links to its oracle evidence.

## Milestone 3 — build a real, leakage-resistant evaluation

Purpose: replace the synthetic-only credibility ceiling with independently reviewable evidence.

Use a non-vendored, license-reviewed Python slice of CodeContests first. Its whole-program
stdin/stdout format is not compatible with the current callable-only product, so selection starts
only after the versioned invocation adapter in Milestone 2 exists. Commit selection manifests,
source URLs, upstream identifiers, licenses, hashes, transformations, and aggregate results—not a
copied multi-gigabyte corpus. Select at least 20 assignments whose input constraints can be mapped
without inventing hidden semantics, plus 500 non-equivalent wrong solutions. Record every
exclusion reason and reject tasks that require an unverifiable custom transformation.

Keep inference and scoring physically separate:

1. The inference process receives only the public assignment context and writes candidate inputs.
2. A separate scorer opens hidden tests, accepted solutions, and frozen thresholds only after the
   candidate file is finalized and hashed.
3. A second human reviewer signs the task filters, accepted controls, and central labels.

Compare equal execution budgets against:

- instructor tests alone;
- uniform random generation;
- deterministic boundary/permutation generation;
- a property-based generator;
- CourseFuzz with and without the model hypothesis provider.

Report per-task and aggregate mutation score, defect recall, false-kill rate, abstention rate,
queries to first finding, total executions, latency, model calls, token cost, and confidence
intervals. Publish failures and excluded cases, not only means.

Exit gate:

- The evaluation replays from hashes in CI or a documented Linux evaluation job.
- Frozen answers remain unavailable to every proposal provider.
- A second reviewer signs the label manifest.
- Public claims use only metrics reproducible from the committed runner and manifests.

## Milestone 4 — make search meaningfully better than random

Purpose: establish technical depth in the central algorithm instead of relying on generated test
ideas.

Add a common budgeted candidate-generator interface. Each generator receives the same sanitized
context and remaining execution budget and returns candidates plus provenance. Start with:

- declared-boundary and permutation generators;
- property-based strategies derived from typed input schemas;
- model-proposed hypotheses;
- survivor-disagreement search that selects inputs predicted to partition the remaining wrong
  programs most strongly;
- coverage-guided generation only after coverage collection is isolated from oracle scoring.

The scheduler should deduplicate candidates globally, record which generator produced each one,
and charge every execution to one shared budget. Use behavioral signatures from executions rather
than exposing reference answers or frozen labels to the model. Keep minimization downstream of a
verified disagreement so shrinking cannot manufacture correctness.

Tests:

- deterministic replay from a recorded random seed and runtime image;
- equal-budget accounting across every baseline;
- generator contract and deduplication tests;
- adversarial cases where plausible model hypotheses are all wrong;
- ablations for each generator and scheduler decision.

Exit gate:

- On the held-out real corpus, CourseFuzz either improves defect recall over random at the same
  execution budget or reaches the same recall with materially fewer executions.
- The improvement is reported with uncertainty and without increasing false kills.
- If this gate fails, keep the honest “verified repair workflow” positioning and continue algorithm
  research; do not relabel a tie as a win.

## Milestone 5 — integrate one real instructor workflow deeply

Purpose: eliminate manual manifest assembly while retaining the verified action loop.

Choose GitHub Classroom/repository workflows first because CourseFuzz already delivers a draft
pull request. Replace deployer-wide tokens with a GitHub App installation scoped to selected
repositories. Build one importer that binds:

- repository and immutable commit;
- assignment configuration and starter code;
- instructor tests and accepted controls;
- anonymized or explicitly consented wrong submissions;
- destination path and branch protection state.

Persist import provenance and webhook delivery IDs. After CourseFuzz opens the draft PR, read back
both the file bytes and the repository CI conclusion before marking the external action verified.
Handle base-branch drift by invalidating approval and regenerating the exact payload.

Exit gate:

- A new instructor can install the integration, select one repository, run the analysis, approve
  the exact patch, and see a verified draft PR without copying tokens or JSON.
- Duplicate webhooks, revoked installations, changed permissions, failed CI, and base drift have
  integration tests and visible recovery states.

Canvas/LTI or another LMS comes later. One complete repository workflow is stronger than several
read-only imports.

## Milestone 6 — move from a single-instance demo to durable operations

Purpose: support multiple courses without weakening approval, isolation, or recovery semantics.

Refactor persistence behind repositories before changing storage:

- Postgres for assignments, runs, approvals, leases, and append-only audit events;
- object storage for immutable source bundles, generated patches, and execution receipts;
- a transactional outbox plus worker queue for analysis and destination actions;
- leased jobs with heartbeat, cancellation, bounded retries, and dead-letter inspection;
- unique database constraints for tenant-scoped idempotency and one-time approval consumption.

Add institutional authentication only when required: OIDC/SSO, course-level roles, least-privilege
service identities, session rotation, and explicit retention/deletion policies. Store no raw student
identity unless the chosen workflow requires it; prefer salted pseudonymous submission IDs.

Operational evidence:

- request, run, execution, and destination IDs in structured logs and traces;
- queue age, sandbox saturation, error class, abstention, cost, and verification-latency metrics;
- tested backup restore, schema migration rollback, secret rotation, and tenant export/deletion;
- load and chaos tests for worker loss, database failover, duplicate messages, and partial writes.

Exit gate:

- Two API instances and multiple workers preserve exactly-once business effects through
  idempotency, even though messages can be delivered more than once.
- A restore exercise recovers a run and its complete audit trail.
- Cross-tenant isolation is tested at route, repository, object-key, and worker-job boundaries.

## Milestone 7 — deepen the review experience

Purpose: help instructors make faster, safer decisions after the backend evidence is credible.

Add only workflow-facing capabilities:

- batch triage ordered by grading impact and confidence policy;
- side-by-side failing execution, oracle evidence, minimized input, exact diff, and affected wrong
  submissions;
- approve, reject with reason, revise destination, retry, and supersede states;
- stale-run and changed-base warnings;
- downloadable audit bundle with assignment hash, execution receipts, approval, patch, read-back,
  and final metrics;
- keyboard-complete review, AA contrast, reduced motion, and non-hidden mobile approval context.

Avoid generic analytics dashboards. Every screen should move evidence toward a decision or explain
why CourseFuzz abstained.

Exit gate:

- Five instructors or course staff can complete the golden path without coaching.
- Their errors, hesitations, rejected recommendations, and changed decisions are recorded as
  qualitative evidence and converted into reviewed product changes.

## Language expansion gate

Do not add a second language merely to enlarge the benchmark. First extract stable `LanguageAdapter`
contracts for validation, compilation, invocation, output normalization, coverage, and sandbox
image selection. Add the next language only when a real course partner or the validated target
corpus demands it. The new adapter must pass the same execution, oracle, security, and evaluation
contract suites as Python.

## First ten implementation issues

1. Publish and verify the current release evidence bundle.
2. Define versioned execution request/result contracts and adapter tests.
3. Implement one no-network remote isolated runner behind `ExecutionGateway`.
4. Commit the hostile-program abuse corpus and deployed containment test.
5. Introduce `OracleDecision` and the four oracle adapters.
6. Add assignment schema versioning and replay migrations.
7. Build the non-vendored real-corpus manifest and two-process scorer.
8. Add equal-budget baseline accounting and confidence-interval reporting.
9. Implement survivor-disagreement search with ablation tests.
10. Replace the shared GitHub token path with a repository-scoped GitHub App import/action loop.

Each issue must include a user-visible outcome, typed contract changes, failure states, tests,
evaluation impact, security impact, and documentation changes. Split an issue when its acceptance
criteria cannot be demonstrated independently.

## Work to reject for now

- multiple agents whose responsibilities are only prompt descriptions;
- automatic merge or grading changes without exact human approval;
- additional dashboards that do not close an instructor decision;
- broad LMS support before one repository integration works end to end;
- C/Java support solely to claim a larger benchmark;
- vector databases, fine-tuning, or learned feedback loops without a measured retrieval/learning
  failure and reviewed data governance;
- claims based on model self-evaluation, mutation score alone, or hidden non-reproducible data.

## Planning rule

At the end of each milestone, rerun the frozen benchmarks, abuse tests, integration tests, browser
golden path, release guard, and security/limitation review. Update this document only when evidence
changes the order. The next milestone is not “done” because code exists; it is done when its exit
gate can be reproduced from a clean environment.
