# Architecture

CourseFuzz is intentionally a narrow vertical slice:

```text
React proof sheet (evidence, exact approval, live/resumable audit trace)
  -> access policy + typed HTTP route
  -> AssignmentService (validation, canonicalization, content-addressed snapshots)
  -> RunService (workflow, approval, idempotency, read-back)
    -> AssessmentEngine (mutation, oracle consensus, minimization, metrics)
      -> HypothesisProvider (GPT-5.6 or deterministic fallback)
      -> ExecutionGateway (versioned ExecutionRequest -> ExecutionResult + runtime-pinned receipt)
        -> LocalRestrictedRunner (restricted syntax, total deadline; development only)
        -> DockerIsolatedRunner (no-network container: cap-drop ALL, read-only root, mem/PID limits)
          -> GVisorDockerRunner (runtime="runsc"; syscall-filtering sandbox for arbitrary code)
    -> DestinationCoordinator
      -> local artifact adapter
      -> GitHub branch + draft-PR adapter
    -> Repository protocol
      -> tenant-scoped SQLite adapter for local/Compose use
      -> tenant-scoped Postgres adapter for hosted workflow and artifact durability
```

## Trust boundary

GPT-5.6 proposes bounded integer inputs and a rationale. It never supplies the expected
answer and never decides whether a finding is true. Two independently authored accepted
solutions must agree before an input receives an expected result. The restricted execution
adapter then reruns each surviving misconception mutant. Candidates without a behavioral
disagreement are rejected.

Execution runs behind a single domain protocol, `ExecutionGateway`, which turns a versioned
`ExecutionRequest` (source SHA, entrypoint, typed cases, resource limits) into a versioned
`ExecutionResult` carrying a runtime-pinned `ExecutionReceipt`. Today the only adapter is
`LocalRestrictedRunner`: it accepts a small Python subset and starts a fresh `python -I` process
with a 1.5-second total deadline and an output ceiling enforced out-of-process. This is suitable
for the seeded demonstration and its containment surface is locked by `tests/test_hostile_corpus.py`,
but it is a source-AST boundary, not hostile-code isolation. `DockerIsolatedRunner` implements the
same gateway against a throwaway container that disables the network, drops all capabilities, mounts
a read-only root, and enforces memory/PID ceilings out of the guest; `GVisorDockerRunner` selects
gVisor's `runsc` runtime, the syscall-filtering boundary appropriate for genuinely untrusted code.
The container's isolation posture lives entirely in its `docker run` argv and is asserted by
`tests/test_docker_isolated_runner.py`; a daemon-gated test runs it end to end. The container adapter
is not yet the default analysis path — the engine still executes through the local runner — so this
is defense-in-depth wiring, and running arbitrary (non-restricted) code additionally needs the
`runsc` runtime, seccomp/user-namespace policy, and image provenance.

A separate worker (`python -m coursefuzz.worker`, backend chosen by
`COURSEFUZZ_EXECUTION_BACKEND=local|docker|gvisor`) claims queued runs from the shared repository
and analyzes them on the selected backend, reusing the tested `recover_incomplete_runs` claim loop.
This is how isolated execution runs off the API process; deploy the API with
`COURSEFUZZ_DEFER_ANALYSIS=1` so runs stay queued for the worker.

## Workflow states

```text
queued -> analyzing -> approval_required -> approved -> applying -> verified
                    \-> failed                 \-> approved (retryable write failure)
```

Every transition writes an ordered database audit event. SSE clients can resume with
`Last-Event-ID`. Run creation is idempotent when the same `Idempotency-Key` is reused.
The run ID is also kept in the browser URL so a reload reconstructs state from the server;
local storage is not treated as workflow persistence.

When deployment keys are configured, bearer or HttpOnly-cookie authentication resolves every
request to a tenant. Assignment access is many-to-many so seeded examples can be explicitly
global without exposing private imports; runs and tenant-prefixed idempotency keys have one owner.
The API verifies ownership before run reads, approvals, writes, artifact downloads, and SSE streams.

Assignment source, controls, tests, domain, and destination are canonicalized into an immutable
SHA-256 snapshot. Every run stores that snapshot hash and refuses execution if its assignment ID
resolves to different content.

## Artifact closure

Approval is bound to the SHA-256 of the exact regression test, affected misconception IDs, and
destination. GitHub targets additionally bind the repository, base branch, base commit SHA,
run-specific head branch, and path. Application writes the approved bytes, reads the destination
back, hashes it, reruns the corpus, and persists a local or pull-request receipt. A successful API
response therefore means write and verification completed; it does not merely mean a button was
clicked.
