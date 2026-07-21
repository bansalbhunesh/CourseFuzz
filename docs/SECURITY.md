# Security

## Implemented in the vertical slice

- Typed Pydantic request, workflow, evidence, and artifact contracts.
- Exact-payload approval using SHA-256 plus a 256-bit random approval token.
- Idempotent run creation and idempotent verified-state reapplication.
- SQLite WAL persistence for workflow state and audit events.
- A fresh isolated Python process, `-I` mode, restricted AST allowlist, no built-ins, 16 KiB
  source limit, 1 MB output limit, and a 1.5-second total execution deadline.
- One bounded GPT-5.6 request with structured output, 1,400 maximum output tokens, a 20-second
  client deadline, one retry, `store=False`, and a non-PII safety identifier.
- Deterministic fallback if GPT-5.6 is unavailable or refuses/malforms its output.
- Destination file read-back and post-write regression verification.
- API keys are environment-only and ignored by Git.

## Deliberate limitations

- The demo has no authentication or tenant isolation and must not be exposed as a shared service.
- The restricted Python process is not a general-purpose hostile-code sandbox. Production needs
  a container or microVM with no network, read-only root, cgroup quotas, seccomp, and per-run
  identity.
- URL/LMS ingestion is not implemented. Any future importer must enforce a domain allowlist,
  robots and license policy, prompt-injection stripping, content hashes, file limits, and code
  quarantine.
- SQLite is durable for a single instance, not a horizontally scaled deployment.
- The seeded programs contain no personal data. No PII redaction pipeline exists yet.

Do not describe the current runner as production-safe arbitrary-code execution.

