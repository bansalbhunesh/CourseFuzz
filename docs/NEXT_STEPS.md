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

## Round 2 product objective

“Used by the masses” means a self-serve service for many independent instructors and course teams,
not an anonymous endpoint that can execute arbitrary code or write through one shared GitHub token.
The Round-2 golden path remains narrow:

> Install CourseFuzz on one autograder repository → select an assignment → observe an
> execution-backed grading gap → approve one exact patch → receive a draft PR → see target CI and
> read-back verification → revisit the durable audit record.

Build the product in four release trains, each independently deployable:

1. **Self-serve trust:** GitHub App installation, user/workspace identity, repository picker,
   installation-token minting, tenant-scoped authorization, onboarding, revocation, and a safe
   read-only sample workspace. Exit only when two test tenants cannot read or write each other's
   assignments, runs, repositories, receipts, or event streams.
2. **Isolated execution:** versioned stdin/stdout contract, separate gVisor worker pool, signed and
   expiring job/receipt envelopes, no network, pinned image digest, hard CPU/memory/output limits,
   queue backpressure, cancellation, and hostile-corpus replay. Exit only when no submitted program
   executes in the web process and every result identifies its runtime image and limits.
3. **Durable operations:** database migrations, transactional outbox, leased jobs, idempotent
   retries, immutable object storage, per-tenant quotas, cost ledger, rate limits, structured
   telemetry, backup/restore drill, and an operator incident view. Exit only after crash/resume,
   duplicate delivery, partial GitHub failure, and restore tests preserve exactly-once approval.
4. **Real-course credibility:** licensed stdin/stdout corpus replay, runtime label validation,
   sealed equal-budget baselines, second human review, uncertainty intervals, five observed
   instructor sessions, accessibility gates, and an LMS importer only after the repository flow is
   stable. Exit only when every public claim is reproducible from committed hashes and reviewed
   evidence.

The first Round-2 production branch implements the credential half of this boundary: exact
repository-to-installation mapping, short-lived repository-scoped tokens, safe caching, visible
health mode, and static-token compatibility. The next slice persists workspace memberships and App
installations from signed callbacks, then exposes the repository picker. The existing
`ExecutionGateway`, atomic approval claim, destination adapter, Postgres repository, and external-CI
state machine are retained rather than rewritten.

## Current baseline

CourseFuzz has the full verified repair workflow plus four deeper foundations: a versioned
`ExecutionGateway` with Docker and gVisor adapters, explicit oracle provenance and abstention,
budgeted/deduplicated candidate generators with a shared execution ledger, and a non-vendored
CodeContests/CodeNet-origin evaluation manifest and hidden-scorer contract. Exact approval
consumption and the `approved -> applying` claim are one database transaction in SQLite and
Postgres. External GitHub delivery has now been proven against the dedicated Demo Target: draft PR
#1 preserves the generated file, passing target CI, and read-back evidence. Every one of those
claims has committed tests or a public external receipt.

The next work must close four honest gates:

1. The public release still lacks the final demo video; the live Demo-Target receipt is preserved.
2. The gVisor worker and live abuse tests exist, but the free Render web service still uses the
   restricted local path; genuinely untrusted programs require a separately deployed runsc worker.
3. The frozen real-corpus selection is not yet a performance result. Stdin/stdout invocation,
   runtime label validation, sealed baseline candidate files, and second-review signoff remain.
4. Deterministic CourseFuzz reaches 95.0% versus 93.3% for equal-budget random-8 on the small
   synthetic corpus. The +1.7-point edge is not a general search-superiority result; that remains
   unestablished until the larger held-out corpus can be replayed.

### Gap 3, measured: why the synthetic edge is not yet general evidence (2026-07-22)

We instrumented the frozen synthetic corpus by executing every mutant and the reference across each
assignment's whole bounded domain and reasoning about kill-sets directly (`scratchpad` probe). The
product itself does not sweep that domain: it verifies at most eight generated candidates in two
bounded execution waves, then selects the verified input with maximum survivor coverage. Aggregate
mutation score by strategy:

| Strategy | Mutation score | Notes |
| --- | --- | --- |
| Instructor tests only | 53.3% | starting point |
| Single added test (product) | **93.3% → 95.0%** | one oracle-backed regression test per assignment |
| Feedback-directed minimal *suite* (offline probe) | 100% | greedy set-cover, ≤2 tests per assignment |
| Random-8 set-cover | 100% | 8 blind samples, greedy set-cover |

Reading: one production repair reaches **95.0% versus random-8's 93.3%**, while both offline
multi-test set-cover probes reach every wrong program. The entire product advantage is one mutant on
a ten-assignment corpus whose domains contain at most 27 inputs. **That is encouraging engineering
evidence, not a superiority claim.** Milestone 3's larger, non-vendored real corpus (with input
spaces a blind sweep cannot saturate) is the honest way to establish or refute a durable advantage.

What shipped from this finding (one-test apply contract unchanged): generators preserve equality
patterns and prioritize legible boundary combinations; accepted controls establish the oracle for
the fixed candidate batch; surviving programs run against that same batch; and the selector chooses
the verified input that discriminates the *most* survivors, tie-broken toward smallness. This lifted
single-test mutation score from 93.3% to 95.0% while replacing a 729-input exhaustive pass with a
bounded eight-candidate proof path suitable for the hosted product.

Pitch line, honest form: *"CourseFuzz selects the independently verified candidate that catches the
most surviving wrong programs at once. It has a 1.7-point edge over equal-budget random-8 on our
small frozen synthetic corpus; the larger held-out evaluation is required before claiming that edge
generalizes."*

## How the two repositories connect

CourseFuzz uses two repositories on purpose. They are connected at runtime through the GitHub API,
not through a Git submodule, package dependency, shared checkout, or workflow token.

| Repository | Responsibility | Must not do |
| --- | --- | --- |
| [`bansalbhunesh/CourseFuzz`](https://github.com/bansalbhunesh/CourseFuzz) | Product source, product CI, Render deployment definition, analysis engine, approvals, Postgres audit trail, and GitHub destination adapter. Render deploys this repository's `main` branch. | It must not receive generated demo patches or contain the GitHub write credential. |
| [`bansalbhunesh/CourseFuzz-Demo-Target`](https://github.com/bansalbhunesh/CourseFuzz-Demo-Target) | Disposable public autograder target containing `solution.py`, the original instructor suite, and its own pytest workflow. It receives only run-specific branches and draft pull requests under `tests/coursefuzz/`. | It must not contain CourseFuzz secrets, real student data, or production course material. |

The two CI paths are independent:

```text
CourseFuzz main -> CourseFuzz CI -> Render auto-deploy -> public CourseFuzz service
                                                        |
                                                        | approved GitHub API write
                                                        v
Demo-Target main <- draft PR <- coursefuzz/<run>-<patch> branch
       |
       `-> Demo-Target pytest CI validates the proposed autograder test
```

### Runtime configuration

The Render service holds the integration credentials. They are never sent to the browser or stored
inside an assignment manifest:

- `COURSEFUZZ_GITHUB_TOKEN` is a fine-grained token limited to
  `bansalbhunesh/CourseFuzz-Demo-Target`, with `Contents: write` and
  `Pull requests: write`.
- `COURSEFUZZ_GITHUB_ALLOWED_REPOS` is independently set to exactly
  `bansalbhunesh/CourseFuzz-Demo-Target`. The adapter fails closed even if a broader token is
  accidentally supplied.
- `/api/health` reports `github_destination: configured` without exposing either value.

An imported assignment selects the second repository with this destination contract:

```json
{
  "destination": {
    "kind": "github_pull_request",
    "repository": "bansalbhunesh/CourseFuzz-Demo-Target",
    "base_branch": "main",
    "test_directory": "tests/coursefuzz"
  }
}
```

The assignment entrypoint must match the target repository's `solution.py`. For the seeded demo it
is `classify_triangle`, so a generated file imports `classify_triangle` from `solution` and can run
inside the target repository's existing pytest workflow.

### Exact write and verification sequence

1. CourseFuzz analyzes the immutable assignment snapshot and independently executes accepted and
   misconception programs. GitHub receives nothing during analysis.
2. Before showing the approval action, the destination adapter reads Demo-Target's current `main`
   SHA. It derives a run-specific branch named `coursefuzz/<run-id>-<patch-suffix>` and binds the
   repository, base branch, base commit, head branch, file path, generated pytest bytes, and affected
   misconceptions into the approval payload hash.
3. The instructor reviews and approves that exact hash. Changing the test or destination requires a
   new approval.
4. CourseFuzz creates the bound branch from the recorded base commit, writes one generated file such
   as `tests/coursefuzz/test_coursefuzz_classify_triangle_<case>.py`, and opens a **draft** pull
   request. It never pushes to Demo-Target `main` and never merges the PR.
5. The adapter reads the file back from the run-specific GitHub branch, compares the exact bytes,
   computes the SHA-256 receipt, reruns the full misconception corpus and accepted controls, and
   persists the PR URL, commit information, artifact hash, and ordered audit events in Postgres.
6. Demo-Target's separate `pull_request` workflow runs `python -m pytest`. CourseFuzz holds the run
   at `external_ci_pending`, polls the target check-runs API, persists the Actions URL and conclusion,
   and advances to `verified` only after success. Failure or timeout is recorded explicitly.

Retries are bounded and idempotent at the destination boundary: if the run branch or draft PR
already exists, CourseFuzz reuses it, converges the run-specific target file to the approved bytes,
and requires the same final read-back check. It does not modify other files or merge the branch.

### Live proof checklist

The two-repository connection is release-proven only when all of the following are captured:

1. The public CourseFuzz health receipt names the deployed `main` commit and reports GitHub as
   configured.
2. A deployed run targets the exact Demo-Target repository and shows its base commit before
   approval.
3. The approved action creates a run-specific branch and draft PR in Demo-Target.
4. The generated file in that branch byte-matches the approved payload and the persisted audit
   receipt has `read_back_verified: true`.
5. Demo-Target pytest CI passes on the draft PR.
6. The public PR URL is recorded as `live_github_receipt_url` in `release_manifest.json`; the demo
   video shows the CourseFuzz approval and the corresponding GitHub PR without editing away the
   transition.

This separation makes the demo safe and legible: the first repository proves the product and its
governance, while the second proves a real external write without risking the product source or a
real course.

## Next-level execution ladder

Use this ladder as the short operational plan. The detailed milestones below define the engineering
requirements; this section defines what to do next and what evidence permits the project to advance.
Do not work on two levels at once when the earlier level's proof is incomplete.

| Level | Current state | Next concrete move | Exit evidence |
| --- | --- | --- | --- |
| 0. Release proof | Public app/repo and live receipt exist; video missing. | Record and publish the exact verified flow. | Public video, captions, logged-out link check, and passing submission guard. |
| 1. Safe execution | Gateway, runc/runsc adapters, receipts, worker, and live abuse CI shipped. | Deploy the worker on a runsc-capable host and replay the full hostile corpus there. | No student code in the API process; deployed runtime-pinned receipts for every execution. |
| 2. Trustworthy truth | Consensus `OracleDecision`, abstention, provenance, UI, and audit shipped. | Add reference/property/fixture adapters and versioned stdin/stdout invocation. | Shared-bug and nondeterministic cases abstain; every displayed output links to evidence. |
| 3. Real evidence | Frozen 20-task/500-wrong manifest, exclusions, leakage boundary, scorer, and CI verifier shipped. | Runtime-validate labels, seal baseline files, and obtain second-review signoff. | Replayable scored results with hashes, uncertainty, costs, and human signoff. |
| 4. Better search | Shared budget, deduplication, provenance, batched verification, and maximum-coverage selection shipped. | Run equal-budget real-corpus ablations and add survivor-disagreement generation if needed. | Higher recall at equal cost, or equal recall with fewer executions, without more false kills. |
| 5. Real instructor workflow | Repository-scoped App tokens plus target-CI read-back shipped; mapping is deployment-managed. | Persist signed App callbacks, workspace memberships, and repository selection. | Install, analyze, approve, verified draft PR, and recovery without copying tokens or JSON. |
| 6. Durable service | Postgres persistence and atomic one-time apply claim shipped; single worker topology only. | Add migrations, transactional outbox, leases, immutable object storage, and restore drills. | Multi-instance chaos test and backup/restore exercise pass. |
| 7. Validated product | Responsive evidence/approval UI exists; no instructor study yet. | Run five observed usability sessions on the evidence-to-approval flow. | Reviewed findings become measured product changes; keyboard, mobile, and AA gates pass. |

### Immediate queue: finish Level 0

Perform these tasks in order. Each task should produce a link or committed artifact, not only a
verbal claim.

1. **Live GitHub receipt — complete.** Demo Target draft PR #3 changes one generated file below
   `tests/coursefuzz/`, targets `main`, passed the target pytest workflow, and matches the persisted
   read-back SHA-256.
2. **Automatic CI closure — complete.** The frontend exposes `external_ci_pending`, polls the
   bounded server-side verifier, and visibly advances only after the target checks pass.
3. **External evidence — complete.** `release_manifest.json` and the README point to the canonical
   public PR; no credential or student data is committed.
4. **Record the demo.** Follow `docs/DEMO_RUNBOOK.md`, keep the recording between 2:50 and 2:55,
   include burned and platform captions, show the actual approval-to-PR transition, test it muted
   and on a phone, and publish the stable video URL.
5. **Close the release gate.** Set `video_url`, change the manifest status to `submission-ready`, run
   `python scripts/release_guard.py --submission`, run the complete CI suite, and verify the public
   app, repository, video, and PR links from a clean logged-out environment.
6. **Preserve Round 1; ship Round 2.** The exact Round-1 commit is tagged
   `build-week-round1-2026-07-21`. Round-2 work is unfrozen, but every release still needs green CI,
   an exact deployed commit receipt, and a replayed golden path.

Level 0 is blocked if the PR is mocked, the generated file differs from the approved payload, target
CI is red, any public link requires the owner's session, or the video hides a failed/retried action.

### Engineering queue after release proof

Once Level 0 passes, implement these remaining slices in dependency order:

1. **Version stdin/stdout invocation.** Add deterministic serialization, schemas, runtime pins,
   migrations, and replay tests while keeping the existing callable path backward-compatible.
2. **Deploy the runsc worker.** Put the existing gVisor adapter on a separate host, require signed
   jobs/receipts, pin the image digest, add queue backpressure, and replay the live abuse corpus.
3. **Runtime-validate the real manifest.** Execute both oracle programs, three controls, and 500
   wrong programs; version every drift exclusion instead of silently dropping failures.
4. **Complete the oracle family.** Add reference, property, and fixture adapters plus repeated-run
   nondeterminism detection; preserve consensus abstention as the default.
5. **Freeze equal-budget candidates.** Produce public, random, boundary/property,
   CourseFuzz-no-model, and CourseFuzz-model files with seeds and shared-ledger cost receipts.
6. **Obtain independent signoff.** Have a second reviewer approve licensing, filters, controls, and
   central labels in the committed reviewer schema.
7. **Score and publish the real evaluation.** Run hidden scoring only after candidate sealing; report
   per-task failures, confidence intervals, abstention, first-finding queries, executions, and costs.
8. **Use the result to choose search work.** Add survivor-disagreement or coverage guidance only if
   the ablation identifies a measured failure; do not optimize to hidden answers.
9. **Replace the demo token with a GitHub App.** Scope installations per repository, import immutable
   course commits, deduplicate webhooks, and retain target-CI read-back before verification.

For each issue, require: a user-visible outcome, typed contract changes, failure and recovery states,
unit plus integration coverage, one end-to-end acceptance test, security impact, evaluation impact,
documentation updates, and an explicit non-goal. Reject an issue that cannot be demonstrated or
verified independently.

### Decision rules for adding breadth

- Do not add Java, C++, an LMS, analytics pages, more agents, or multi-user administration while
  execution isolation, oracle provenance, and real-corpus evidence remain below their gates.
- If the real evaluation still ties random search, keep the product positioned as a verified repair
  workflow; prioritize the scheduler and candidate generators rather than marketing a false win.
- Prefer a dated instructor review, changed decision, or signed label set over another speculative
  feature.
- Re-run the prior level's acceptance suite after every architecture change. A higher level cannot
  invalidate the evidence that justified the previous one.

## Milestone 0 — finish the public proof loop

Purpose: turn the existing implementation into judge-verifiable evidence before changing the core.

Work:

- Completed: merge the reviewed product branch into `main`, keep CI green, and remove the obsolete
  source branch only after Render tracks `main`.
- Completed: create the dedicated `CourseFuzz-Demo-Target` repository instead of using the product
  repository as a write target.
- Completed: deploy the zero-cost `render.yaml` shape with required authentication, hosted Postgres,
  the single-repository GitHub allowlist, and immutable commit evidence.
- Completed: execute the deployed GitHub destination flow and preserve public draft PR #3 with
  byte read-back and passing target CI.
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

Status: **implementation and CI proof complete; separate worker deployment remains open.**

Code shape:

```text
AssessmentEngine
  -> ExecutionGateway (domain protocol)
       -> LocalRestrictedRunner (development only)
       -> DockerIsolatedRunner (runc defense in depth)
       -> GVisorDockerRunner (runsc target; one ephemeral sandbox per batch)
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

Status: **consensus decision, abstention, provenance, UI, and audit complete; additional oracle
adapters plus invocation/schema versioning remain open.**

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

Status: **selection, provenance, exclusions, leakage boundary, scorer contract, confidence intervals,
and manual replay workflow complete; scored claims remain blocked by the gates below.**

Current Phase-5 branch progress: the pinned acquisition, 20-task/500-wrong-program manifest,
complete exclusion ledger, public-only bundle, candidate receipt, equal-budget validation, hidden
scorer contract, confidence intervals, and manual Linux replay workflow are implemented. Raw data
remains non-vendored. Runtime label validation, baseline candidate files, cost-ledger joins, and
second-review signoff remain open; no real-corpus performance claim exists yet.

Use a non-vendored, license-reviewed Python slice of CodeContests first. Its whole-program
stdin/stdout format is not compatible with the current callable-only product, so execution-backed
label validation and scoring start only after the versioned invocation adapter in Milestone 2
exists. Hash-only selection can be prepared independently. Commit selection manifests,
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

Status: **budgeted generator contracts, boundary/permutation generators, global deduplication,
provenance, shared execution accounting, batched verification, and maximum-coverage selection complete; real-corpus
ablations and any evidence-driven generator additions remain open.**

Add a common budgeted candidate-generator interface. Each generator receives the same sanitized
context and remaining execution budget and returns candidates plus provenance. Start with:

- declared-boundary and permutation generators;
- property-based strategies derived from typed input schemas;
- model-proposed hypotheses;
- survivor-disagreement search that selects inputs predicted to partition the remaining wrong
  programs most strongly. Add it only if real-corpus ablations show the present bounded boundary,
  permutation, and model-generated candidate batch is insufficient;
- coverage-guided generation only after coverage collection is isolated from oracle scoring.

The scheduler should deduplicate candidates globally, record which generator produced each one,
and charge every execution to one shared budget. Use behavioral signatures from executions rather
than exposing reference answers or frozen labels to the model. Any future shrinker must re-verify
the shrunken candidate and preserve or improve survivor coverage.

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

Harden the existing repository boundary for multi-instance operation:

- evolve Postgres assignments, runs, approvals, and audit events with explicit migrations and
  leases;
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
- side-by-side failing execution, oracle evidence, selected verified input, exact diff, and affected wrong
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

## Next ten implementation issues

1. Publish and verify the live Demo-Target receipt and demo video.
2. Add versioned stdin/stdout invocation plus snapshot migrations.
3. Deploy the existing gVisor worker on a runsc-capable host.
4. Runtime-validate the 20-task/500-wrong frozen manifest.
5. Add reference, property, and fixture oracle adapters with nondeterminism checks.
6. Freeze equal-budget baseline and CourseFuzz candidate files with cost receipts.
7. Obtain second-review signoff for licensing, controls, and labels.
8. Score and publish the held-out evaluation with uncertainty and failures.
9. Run generator ablations and implement only the search improvement they justify.
10. Replace the shared GitHub token with a repository-scoped GitHub App workflow.

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
