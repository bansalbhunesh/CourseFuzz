# Edge-case matrix

Status: `covered`, `bounded`, or `release blocker`.

| Case | Expected behavior | Evidence | Status |
|---|---|---|---|
| Model quota/latency | One retry, 20 s deadline, then deterministic hypotheses | provider contract | bounded |
| Partial model failure/refusal | No partial payload reaches execution; use deterministic fallback | structured output + fallback | bounded |
| Duplicate create request | Same idempotency key returns the original run | API golden-path test | covered |
| Crash/resume during analysis | Resume queued/analyzing work from durable state | recovery worker not built | release blocker |
| Stale assignment data | Bind runs to a content-addressed assignment snapshot | seeded fixture only | bounded |
| Malformed model output | SDK schema parse fails closed, then fallback | typed provider boundary | bounded |
| Low-confidence oracle | Abstain when accepted solutions disagree | consensus code path | covered |
| Approval rejection/mismatch | Reject any hash other than the exact patch payload | API golden-path test | covered |
| Write failure | Restore approved state, record event, allow safe retry | service recovery path | bounded |
| Auth/tenant boundary | Reject unauthenticated cross-tenant access | not implemented | release blocker |
| Prompt injection | Page text never becomes instructions | URL ingestion not implemented | bounded |
| URL/file ingestion | Allowlist, license proof, content hash, quarantine | not implemented | release blocker |
| PII redaction | Detect/redact before persistence or model calls | seeded data has no PII | release blocker |
| Cost limit | One model call, 1,400 max output tokens, no tool loop | provider contract | covered |
| Sandbox timeout | Terminate process at total deadline and return structured evidence | sandbox test boundary | covered |
| SSE reconnect | Resume strictly after `Last-Event-ID` without duplicate event | API SSE test | covered |
| Deployment drift | Show commit SHA and run clean deployed smoke test | deployment not created | release blocker |
| Accepted-solution false kill | Block closure unless all accepted solutions still pass | engine metric + test | covered |
| Destination mismatch | Fail if file bytes or rerun metrics differ from approved projection | API golden-path test | covered |

No release should call the project production-ready while any security or public-demo row remains a
release blocker.

