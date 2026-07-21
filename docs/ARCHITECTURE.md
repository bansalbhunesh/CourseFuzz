# Architecture

CourseFuzz is intentionally a narrow vertical slice:

```text
React proof sheet (evidence, exact approval, live/resumable audit trace)
  -> HTTP route
  -> RunService (workflow, approval, idempotency, read-back)
    -> AssessmentEngine (mutation, oracle consensus, minimization, metrics)
      -> HypothesisProvider (GPT-5.6 or deterministic fallback)
      -> SubprocessPythonSandbox (restricted syntax, total deadline)
    -> RunRepository (SQLite runs, approvals, events, artifacts)
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

Every transition writes an ordered SQLite audit event. SSE clients can resume with
`Last-Event-ID`. Run creation is idempotent when the same `Idempotency-Key` is reused.
The run ID is also kept in the browser URL so a reload reconstructs state from the server;
local storage is not treated as workflow persistence.

## Artifact closure

Approval is bound to the SHA-256 of the exact regression-test payload. Application writes the
approved file, reads the destination bytes back, hashes them, reruns the mutant corpus, and
persists the artifact hash. A successful API response therefore means the write and verification
both completed; it does not merely mean a button was clicked.
