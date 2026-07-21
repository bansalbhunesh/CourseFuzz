# Architecture

CourseFuzz is intentionally a narrow vertical slice:

```text
React proof sheet (evidence, exact approval, live/resumable audit trace)
  -> access policy + typed HTTP route
  -> AssignmentService (validation, canonicalization, content-addressed snapshots)
  -> RunService (workflow, approval, idempotency, read-back)
    -> AssessmentEngine (mutation, oracle consensus, minimization, metrics)
      -> HypothesisProvider (GPT-5.6 or deterministic fallback)
      -> SubprocessPythonSandbox (restricted syntax, total deadline)
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

The demo runner accepts a small Python subset and starts a fresh `python -I` process with a
1.5-second total deadline. This is suitable for the seeded demonstration; a production
multi-tenant service must replace it with a hardened container or microVM boundary.

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
