# Edge-case matrix

Status: `covered`, `bounded`, or `release blocker`.

| Case | Expected behavior | Evidence | Status |
|---|---|---|---|
| Model quota/latency | One retry, 20 s deadline, then deterministic hypotheses | provider contract | bounded |
| Partial model failure/refusal | No partial payload reaches execution; use deterministic fallback | structured output + fallback | bounded |
| Duplicate create request | Same idempotency key returns the original run | API golden-path test | covered |
| Crash/resume during analysis | Replay queued/analyzing runs; restore interrupted apply to reauthorization | recovery tests + startup worker | covered |
| Stale assignment data | Bind runs to a content-addressed assignment snapshot | immutable snapshot + run hash | covered |
| Malformed model output | SDK schema parse fails closed, then fallback | typed provider boundary | bounded |
| Low-confidence oracle | Abstain when accepted solutions disagree | consensus code path | covered |
| Approval rejection/mismatch | Reject any hash other than the exact patch payload | API golden-path test | covered |
| Write failure | Restore approved state, record event, allow safe retry | local/GitHub adapter recovery path | covered |
| Auth/tenant boundary | Reject unauthenticated and cross-tenant assignment/run/action access | access-policy and isolation tests | covered |
| Prompt injection | Page text never becomes instructions | URL ingestion not implemented | bounded |
| URL/file ingestion | Allowlist, license proof, content hash, quarantine | not implemented | release blocker |
| PII redaction | Detect/redact before persistence or model calls | seeded data has no PII | release blocker |
| Cost limit | One model call, 1,400 max output tokens, no tool loop | provider contract | covered |
| Sandbox timeout | Terminate process at total deadline and return structured evidence | sandbox test boundary | covered |
| SSE reconnect | Resume strictly after `Last-Event-ID` without duplicate event | API SSE test | covered |
| Deployment drift | Show commit SHA and run clean deployed smoke test | deployment not created | release blocker |
| Accepted-solution false kill | Block closure unless all accepted solutions still pass | engine metric + test | covered |
| Destination mismatch | Fail if file bytes or rerun metrics differ from approved projection | API golden-path test | covered |
| GitHub branch/PR duplicate | Reuse the run-specific branch/PR and verify exact bytes | fake-transport adapter test | covered |
| GitHub base drift | Bind approval to the resolved base commit before branch creation | prepared target payload | covered |
| Frozen-evaluation leakage | Providers receive no source, oracle outputs, witnesses, or thresholds | sanitized context test + post-inference threshold load | covered |
| Real-course generalization | License-reviewed external corpus and independent human labels | synthetic v1 is not sufficient | release blocker |
| Missing submission evidence | Fail release if app, video, repo, or live GitHub proof URL is absent | release manifest + submission guard test | covered |

No release should call the project production-ready while any security or public-demo row remains a
release blocker.
