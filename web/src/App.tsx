import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { AssignmentImportDialog } from "./AssignmentImportDialog";

type RunStatus =
  | "queued"
  | "analyzing"
  | "approval_required"
  | "approved"
  | "applying"
  | "external_ci_pending"
  | "external_ci_failed"
  | "verified"
  | "no_action_required"
  | "failed";

type JsonAtom = string | number | boolean;

type Demo = {
  id: string;
  title: string;
  summary: string;
  language: string;
  entrypoint: string;
  instructor_tests: Array<{ inputs: number[]; expected: JsonAtom; label: string }>;
  mutant_count: number;
  accepted_solution_count: number;
  mode: "live-gpt-5.6" | "deterministic-fallback";
};

type AssignmentSummary = {
  id: string;
  snapshot_sha256: string;
  provenance: "seeded" | "manual";
  title: string;
};

type AssignmentSnapshot = {
  id: string;
  snapshot_sha256: string;
  provenance: "seeded" | "manual";
  spec: {
    title: string;
    summary: string;
    language: string;
    entrypoint: string;
    instructor_tests: Array<{ inputs: number[]; expected: JsonAtom; label: string }>;
    mutants: unknown[];
    accepted_solutions: unknown[];
  };
};

type Health = {
  mode: "live-gpt-5.6" | "deterministic-fallback";
  auth: "required" | "local-demo";
};

type Metrics = {
  total_mutants: number;
  killed_mutants: number;
  surviving_mutants: number;
  mutation_score: number;
  accepted_solution_pass_rate: number;
};

type Verdict = {
  hypothesis: {
    id: string;
    inputs: number[];
    rationale: string;
    misconception: string;
    provider: string;
  };
  status: "rejected" | "verified";
  reason: string;
  expected: JsonAtom | null;
  actual: JsonAtom | null;
  killed_mutants: string[];
};

type Analysis = {
  before: Metrics;
  projected_after: Metrics;
  survivors_before: string[];
  hypothesis_verdicts: Verdict[];
  candidate: {
    id: string;
    test: { inputs: number[]; expected: JsonAtom; label: string; source: string };
    observed_actual: JsonAtom | null;
    rationale: string;
    target_mutants: string[];
    payload_sha256: string;
    pytest_source: string;
    target: {
      kind: "local_artifact" | "github_pull_request";
      path: string;
      repository: string | null;
      base_branch: string | null;
      base_commit_sha: string | null;
      head_branch: string | null;
    };
  } | null;
  evidence: Record<string, unknown>;
};

type OracleEvidence = {
  decision?: "resolved" | "abstained" | "no_counterexample";
  provenance?: string;
  sources?: string[];
  quorum?: number;
  abstention_reasons?: string[];
  controls?: number;
};

type Run = {
  id: string;
  assignment_id: string;
  status: RunStatus;
  mode: "live-gpt-5.6" | "deterministic-fallback";
  created_at: string;
  updated_at: string;
  analysis: Analysis | null;
  approval_payload_sha256: string | null;
  artifact_sha256: string | null;
  action_receipt: {
    kind: "local_artifact" | "github_pull_request";
    path: string;
    artifact_sha256: string;
    read_back_verified: boolean;
    external_url: string | null;
    repository: string | null;
    base_commit_sha: string | null;
    commit_sha: string | null;
    pull_request_number: number | null;
  } | null;
  error: string | null;
};

type AuditEvent = {
  id: number;
  event_type: string;
  stage: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type ApprovalReceipt = {
  approval_token: string;
  payload_sha256: string;
  approved_at: string;
};

const proofSteps = [
  { key: "queued", label: "Scan", detail: "baseline" },
  { key: "analyzing", label: "Attack", detail: "hypotheses" },
  { key: "approval_required", label: "Prove", detail: "execution" },
  { key: "approved", label: "Approve", detail: "exact patch" },
  { key: "verified", label: "Verify", detail: "read-back" },
] as const;

const statusOrder: Record<RunStatus, number> = {
  queued: 0,
  analyzing: 1,
  approval_required: 2,
  approved: 3,
  applying: 3,
  external_ci_pending: 4,
  external_ci_failed: 4,
  verified: 4,
  no_action_required: 2,
  failed: 4,
};

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(body.detail ?? "The request failed.", response.status);
  }
  return response.json() as Promise<T>;
}

function oracleEvidenceOf(analysis: Analysis | null | undefined): OracleEvidence | null {
  const raw = analysis?.evidence?.oracle_evidence;
  return raw && typeof raw === "object" ? (raw as OracleEvidence) : null;
}

function pct(value: number) {
  return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
}

function shortHash(value: string | null | undefined) {
  return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : "not issued";
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function assignmentToDemo(snapshot: AssignmentSnapshot, mode: Health["mode"]): Demo {
  return {
    id: snapshot.id,
    title: snapshot.spec.title,
    summary: snapshot.spec.summary,
    language: snapshot.spec.language,
    entrypoint: snapshot.spec.entrypoint,
    instructor_tests: snapshot.spec.instructor_tests,
    mutant_count: snapshot.spec.mutants.length,
    accepted_solution_count: snapshot.spec.accepted_solutions.length,
    mode,
  };
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [demo, setDemo] = useState<Demo | null>(null);
  const [assignments, setAssignments] = useState<AssignmentSummary[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [receipt, setReceipt] = useState<ApprovalReceipt | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [accessToken, setAccessToken] = useState("");
  const [sessionRevision, setSessionRevision] = useState(0);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      try {
        const params = new URLSearchParams(window.location.search);
        const currentHealth = await api<Health>("/api/health");
        if (!active) return;
        setHealth(currentHealth);
        const available = await api<AssignmentSummary[]>("/api/assignments");
        if (!active) return;
        setNeedsAuth(false);
        setAssignments(available);

        const savedRun = params.get("run");
        let assignmentId = params.get("assignment") ?? "triangle-classifier";
        if (savedRun) {
          const saved = await api<Run>(`/api/runs/${savedRun}`);
          if (!active) return;
          setRun(saved);
          assignmentId = saved.assignment_id;
        }
        const snapshot = await api<AssignmentSnapshot>(`/api/assignments/${assignmentId}`);
        if (active) setDemo(assignmentToDemo(snapshot, currentHealth.mode));
      } catch (reason) {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) {
          setNeedsAuth(true);
          setError(null);
        } else {
          setError(reason instanceof Error ? reason.message : "Could not load the workspace.");
        }
      }
    }
    void bootstrap();
    return () => {
      active = false;
    };
  }, [sessionRevision]);

  useEffect(() => {
    eventSourceRef.current?.close();
    if (!run) return;

    const source = new EventSource(`/api/runs/${run.id}/events`);
    eventSourceRef.current = source;
    const receive = (event: MessageEvent<string>) => {
      const item = JSON.parse(event.data) as AuditEvent;
      setEvents((current) =>
        current.some((entry) => entry.id === item.id) ? current : [...current, item],
      );
    };
    const names = [
      "run.created",
      "run.recovered",
      "analysis.started",
      "analysis.hypotheses",
      "analysis.verified",
      "analysis.no_finding",
      "approval.required",
      "approval.granted",
      "patch.applying",
      "patch.verified",
      "patch.failed",
      "external_ci.pending",
      "external_ci.verified",
      "external_ci.failed",
      "run.failed",
    ];
    names.forEach((name) => source.addEventListener(name, receive as EventListener));
    source.addEventListener("stream.paused", () => {
      source.close();
      api<Run>(`/api/runs/${run.id}`).then(setRun).catch((reason: Error) => setError(reason.message));
    });
    source.onerror = () => {
      source.close();
      api<Run>(`/api/runs/${run.id}`).then(setRun).catch(() => setError("Live trace disconnected. Retry the run status."));
    };
    return () => source.close();
  }, [run?.id, run?.status]);

  useEffect(() => {
    if (!run || run.status !== "external_ci_pending") return;

    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await api<Run>(`/api/runs/${run.id}/external-ci`, { method: "POST" });
        if (cancelled) return;
        setRun(next);
        if (next.status === "external_ci_pending") {
          timer = window.setTimeout(() => void poll(), 2_000);
        }
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Could not read the target CI status.");
        timer = window.setTimeout(() => void poll(), 4_000);
      }
    };

    timer = window.setTimeout(() => void poll(), 1_000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [run?.id, run?.status]);

  const activeStep = run ? statusOrder[run.status] : -1;

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<{ tenant_id: string }>("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: accessToken }),
      });
      setAccessToken("");
      setNeedsAuth(false);
      setSessionRevision((current) => current + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    await fetch("/api/session", { method: "DELETE", credentials: "same-origin" });
    eventSourceRef.current?.close();
    setDemo(null);
    setRun(null);
    setAssignments([]);
    setNeedsAuth(true);
  }

  async function selectAssignment(assignmentId: string) {
    setBusy(true);
    setError(null);
    try {
      const [snapshot, health] = await Promise.all([
        api<AssignmentSnapshot>(`/api/assignments/${assignmentId}`),
        api<Health>("/api/health"),
      ]);
      setDemo(assignmentToDemo(snapshot, health.mode));
      setRun(null);
      setEvents([]);
      setReceipt(null);
      setReviewed(false);
      window.history.replaceState({}, "", `?assignment=${assignmentId}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the assignment.");
    } finally {
      setBusy(false);
    }
  }

  async function handleImported(assignmentId: string) {
    const available = await api<AssignmentSummary[]>("/api/assignments");
    setAssignments(available);
    await selectAssignment(assignmentId);
  }

  async function startRun() {
    setBusy(true);
    setError(null);
    setEvents([]);
    setReceipt(null);
    setReviewed(false);
    try {
      const value = await api<Run>("/api/runs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ assignment_id: demo?.id ?? "triangle-classifier" }),
      });
      setRun(value);
      window.history.replaceState({}, "", `?assignment=${value.assignment_id}&run=${value.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the run.");
    } finally {
      setBusy(false);
    }
  }

  async function issueApproval() {
    if (!run?.analysis?.candidate) return;
    setBusy(true);
    setError(null);
    try {
      const value = await api<ApprovalReceipt>(`/api/runs/${run.id}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload_sha256: run.analysis.candidate.payload_sha256 }),
      });
      setReceipt(value);
      setRun(await api<Run>(`/api/runs/${run.id}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!reviewed) return;
    await issueApproval();
  }

  async function applyAndVerify() {
    if (!run || !receipt) return;
    setBusy(true);
    setError(null);
    try {
      setRun((current) => (current ? { ...current, status: "applying" } : current));
      const value = await api<Run>(`/api/runs/${run.id}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_token: receipt.approval_token }),
      });
      setRun(value);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Write verification failed.");
      setRun(await api<Run>(`/api/runs/${run.id}`).catch(() => run));
    } finally {
      setBusy(false);
    }
  }

  if (needsAuth) {
    return (
      <main className="sign-in-shell">
        <section className="sign-in-panel" aria-labelledby="sign-in-heading">
          <a className="wordmark" href="/" aria-label="CourseFuzz home">
            <span className="wordmark-mark" aria-hidden="true">CF</span>
            <span>CourseFuzz</span>
          </a>
          <div>
            <span className="section-number">PROTECTED WORKSPACE</span>
            <h1 id="sign-in-heading">Enter your instructor access key.</h1>
            <p>The key is exchanged for an HttpOnly session and is never stored in browser storage.</p>
          </div>
          {error && <p className="sign-in-error" role="alert">{error}</p>}
          <form onSubmit={signIn}>
            <label htmlFor="access-key">Access key</label>
            <input
              id="access-key"
              type="password"
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              autoComplete="current-password"
              required
            />
            <button className="primary-action full" type="submit" disabled={busy}>
              {busy ? "Checking key…" : "Open workspace"}<span aria-hidden="true">→</span>
            </button>
          </form>
        </section>
      </main>
    );
  }

  if (!demo && !error) {
    return <main className="center-state" aria-live="polite">Loading the assignment workspace…</main>;
  }

  const oracle = oracleEvidenceOf(run?.analysis);

  return (
    <main className="app-shell">
      <header className="masthead">
        <a className="wordmark" href="/" aria-label="CourseFuzz home">
          <span className="wordmark-mark" aria-hidden="true">CF</span>
          <span>CourseFuzz</span>
        </a>
        <div className="masthead-tools">
          <label className="assignment-switcher">
            <span className="sr-only">Current assignment</span>
            <select
              value={demo?.id ?? ""}
              onChange={(event) => void selectAssignment(event.target.value)}
              disabled={busy || Boolean(run && ["queued", "analyzing", "applying", "external_ci_pending"].includes(run.status))}
            >
              {assignments.map((assignment) => (
                <option value={assignment.id} key={assignment.id}>{assignment.title}</option>
              ))}
            </select>
          </label>
          <button className="text-action" type="button" onClick={() => setImportOpen(true)}>Import assignment</button>
          {health?.auth === "required" && <button className="text-action" type="button" onClick={() => void signOut()}>Sign out</button>}
          <div className="run-meta">
            <span className="mode-dot" aria-hidden="true" />
            <span>{demo?.mode === "live-gpt-5.6" ? "GPT-5.6 hypotheses" : "Deterministic fallback"}</span>
          </div>
        </div>
      </header>

      <nav className="proof-rail" aria-label="Run progress">
        {proofSteps.map((step, index) => {
          const done = activeStep > index || run?.status === "verified";
          const current = activeStep === index;
          return (
            <div className={`proof-step ${done ? "is-done" : ""} ${current ? "is-current" : ""}`} key={step.key}>
              <span className="step-index">{done ? "✓" : `0${index + 1}`}</span>
              <span><strong>{step.label}</strong><small>{step.detail}</small></span>
            </div>
          );
        })}
      </nav>

      {error && (
        <div className="error-banner" role="alert">
          <strong>Run interrupted.</strong> {error}
          <button type="button" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <section className="hero">
        <div className="eyebrow">Assessment integrity / {demo?.language}</div>
        <h1>{run?.analysis?.candidate ? "One wrong solution still passes." : run?.analysis ? "No executable blind spot survived." : "Your tests pass. A wrong solution does too."}</h1>
        <p>{run?.analysis?.candidate ? run.analysis.candidate.rationale : run?.analysis ? "Every supplied misconception program is caught, or every bounded hypothesis was rejected by execution." : demo?.summary}</p>
        {!run && (
          <button className="primary-action" type="button" onClick={startRun} disabled={busy || !demo}>
            {busy ? "Opening run…" : "Red-team this suite"}
            <span aria-hidden="true">→</span>
          </button>
        )}
      </section>

      <div className="workspace">
        <section className="case-file" aria-labelledby="case-heading">
          <div className="section-heading">
            <div>
              <span className="section-number">CASE 01</span>
              <h2 id="case-heading">{demo?.title}</h2>
            </div>
            <span className={`status-stamp status-${run?.status ?? "idle"}`}>
              {(run?.status ?? "ready").replaceAll("_", " ")}
            </span>
          </div>

          {!run && demo && (
            <div className="baseline-sheet">
              <div className="metric-ledger">
                <div><span>{demo.instructor_tests.length}</span><small>instructor tests</small></div>
                <div><span>{demo.mutant_count}</span><small>plausible wrong programs</small></div>
                <div><span>{demo.accepted_solution_count}</span><small>accepted controls</small></div>
              </div>
              <h3>What the suite currently asserts</h3>
              <ol className="test-ledger">
                {demo.instructor_tests.map((test) => (
                  <li key={`${test.label}-${test.inputs.join("-")}`}>
                    <code>{demo.entrypoint}({test.inputs.join(", ")})</code>
                    <span>→ {String(test.expected)}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {run && !run.analysis && run.status !== "failed" && (
            <div className="analyzing-state" aria-live="polite">
              <div className="scanner" aria-hidden="true"><span /></div>
              <div>
                <h3>Execution is separating guesses from evidence.</h3>
                <p>Running the instructor suite against eight bounded misconception programs.</p>
              </div>
            </div>
          )}

          {run?.status === "failed" && (
            <div className="failed-state">
              <span>!</span><div><h3>Analysis abstained.</h3><p>{run.error ?? "The run did not produce a safe candidate."}</p></div>
            </div>
          )}

          {run?.analysis && (
            <>
              <div className="score-proof" aria-label={`Mutation score improves from ${pct(run.analysis.before.mutation_score)} to ${pct(run.analysis.projected_after.mutation_score)}`}>
                <div className="score-copy">
                  <span className="kicker">Mutation score</span>
                  <strong>{pct(run.analysis.before.mutation_score)}</strong>
                  <span className="score-arrow" aria-hidden="true">→</span>
                  <strong>{pct(run.analysis.projected_after.mutation_score)}</strong>
                </div>
                <div className="score-track" aria-hidden="true">
                  <span className="score-before" style={{ width: pct(run.analysis.before.mutation_score) }} />
                  <span className="score-after" />
                </div>
                <div className="score-footnote">
                  <span>{run.analysis.before.surviving_mutants} survivor{run.analysis.before.surviving_mutants === 1 ? "" : "s"} before</span>
                  <span>{pct(run.analysis.projected_after.accepted_solution_pass_rate)} accepted controls pass</span>
                </div>
              </div>

              {run.analysis.candidate && (
                <article className="finding">
                  <header>
                    <span className="proof-mark">PROVEN</span>
                    <div>
                      <span className="kicker">Smallest counterexample</span>
                      <h3>{run.analysis.candidate.test.label.replace("CourseFuzz regression: ", "")}</h3>
                    </div>
                  </header>
                  <div className="counterexample">
                    <div><small>INPUT</small><code>({run.analysis.candidate.test.inputs.join(", ")})</code></div>
                    <div><small>REFERENCE</small><strong>{String(run.analysis.candidate.test.expected)}</strong></div>
                    <div className="wrong-output"><small>WRONG PROGRAM</small><strong>{String(run.analysis.candidate.observed_actual ?? "not captured")}</strong></div>
                  </div>
                  {oracle?.decision === "resolved" && (
                    <p className="oracle-provenance">
                      <small>ORACLE</small> Expected output established by {oracle.provenance}
                      {typeof oracle.quorum === "number" ? ` · quorum ${oracle.quorum}` : ""}
                      {oracle.sources?.length ? ` · ${oracle.sources.join(", ")}` : ""}
                    </p>
                  )}
                  <p>Execution reproduced the disagreement after minimizing the generated hypothesis.</p>
                  <footer>
                    <span>Execution-backed</span>
                    <span>{run.analysis.candidate.target_mutants.length} mutant{run.analysis.candidate.target_mutants.length === 1 ? "" : "s"} killed</span>
                    <span>source: {run.analysis.candidate.test.source}</span>
                  </footer>
                </article>
              )}

              {run.analysis.candidate && <section className="patch-proof" aria-labelledby="patch-heading">
                <div className="section-heading compact">
                  <div><span className="section-number">PROPOSED PATCH</span><h3 id="patch-heading">One test closes the gap</h3></div>
                  <span className="hash-label">SHA {shortHash(run.analysis.candidate.payload_sha256)}</span>
                </div>
                <pre><code>{run.analysis.candidate.pytest_source}</code></pre>
                <dl className="evidence-notes">
                  <div><dt>Scope</dt><dd>One generated pytest</dd></div>
                  <div><dt>Control check</dt><dd>{pct(run.analysis.projected_after.accepted_solution_pass_rate)} accepted solutions pass</dd></div>
                  <div><dt>Write target</dt><dd>{run.analysis.candidate.target.repository ? `${run.analysis.candidate.target.repository}/` : ""}{run.analysis.candidate.target.path}</dd></div>
                </dl>
              </section>}
            </>
          )}
        </section>

        <aside className="action-column" aria-labelledby="trace-heading">
          <div className="action-block">
            <span className="section-number">ACTION BOUNDARY</span>
            {!run?.analysis && (
              <><h2>No write without proof.</h2><p>CourseFuzz can inspect and execute freely. Writing a generated test requires review of the exact payload.</p></>
            )}
            {run?.analysis?.candidate && run.status === "approval_required" && (
              <>
                <h2>Approve this exact test?</h2>
                <p>The approval token will be bound to this payload hash. Any content change invalidates it.</p>
                <div className="destination-proof">
                  <small>DESTINATION</small>
                  <strong>{run.analysis.candidate.target.repository ?? "CourseFuzz artifact store"}</strong>
                  <code>{run.analysis.candidate.target.path}</code>
                  {run.analysis.candidate.target.base_commit_sha && <><small>BASE COMMIT</small><code>{shortHash(run.analysis.candidate.target.base_commit_sha)}</code></>}
                </div>
                <label className="review-check">
                  <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
                  <span>I reviewed the input, expected output, and source above.</span>
                </label>
                <button className="primary-action full" type="button" disabled={!reviewed || busy} onClick={approve}>
                  {busy ? "Binding approval…" : "Approve exact payload"}<span aria-hidden="true">→</span>
                </button>
              </>
            )}
            {run?.status === "no_action_required" && oracle?.decision === "abstained" && (
              <div className="verified-result abstained">
                <span className="verified-check" aria-hidden="true">!</span>
                <h2>CourseFuzz abstained.</h2>
                <p>A program survived the instructor suite, but the independent oracle could not establish the correct output on the disputed input, so no accusation was made:</p>
                <ul className="abstention-reasons">
                  {(oracle.abstention_reasons ?? []).map((reason) => <li key={reason}>{reason}</li>)}
                </ul>
              </div>
            )}
            {run?.status === "no_action_required" && oracle?.decision !== "abstained" && (
              <div className="verified-result">
                <span className="verified-check" aria-hidden="true">✓</span>
                <h2>No write proposed.</h2>
                <p>The supplied misconception corpus has no surviving executable gap. The audit trail records the bounded result.</p>
              </div>
            )}
            {run?.status === "approved" && receipt && (
              <>
                <span className="approval-seal" aria-hidden="true">APPROVED</span>
                <h2>Approval is bound.</h2>
                <p>Now write the artifact, read it back from the destination, and rerun the full corpus.</p>
                <div className="receipt"><small>PAYLOAD SHA-256</small><code>{shortHash(receipt.payload_sha256)}</code></div>
                <button className="primary-action full" type="button" disabled={busy} onClick={applyAndVerify}>
                  {busy ? "Writing + verifying…" : "Apply and verify"}<span aria-hidden="true">→</span>
                </button>
              </>
            )}
            {run?.status === "approved" && !receipt && run.analysis?.candidate && (
              <>
                <span className="approval-seal" aria-hidden="true">PERSISTED</span>
                <h2>Approval survived the interruption.</h2>
                <p>Reissue a short-lived action token for the same exact payload, then resume the idempotent write and read-back.</p>
                <div className="receipt"><small>APPROVED PAYLOAD</small><code>{shortHash(run.approval_payload_sha256)}</code></div>
                <button className="primary-action full" type="button" disabled={busy} onClick={() => void issueApproval()}>
                  {busy ? "Reauthorizing…" : "Reauthorize exact action"}<span aria-hidden="true">→</span>
                </button>
              </>
            )}
            {run?.status === "applying" && (
              <><h2>Reading back the destination…</h2><p>The result is not complete until the written bytes and full rerun agree.</p></>
            )}
            {run?.status === "external_ci_pending" && (
              <div className="verified-result pending">
                <span className="verified-check" aria-hidden="true">…</span>
                <h2>Draft PR opened. Read back. Awaiting target CI.</h2>
                <p>The approved bytes already match the destination. CourseFuzz is now reading the target repository's own checks before it can call the action verified.</p>
                {run.action_receipt?.external_url && (
                  <a className="download-link" href={run.action_receipt.external_url} target="_blank" rel="noreferrer">Open pending draft pull request <span aria-hidden="true">↗</span></a>
                )}
              </div>
            )}
            {run?.status === "external_ci_failed" && (
              <div className="verified-result abstained">
                <span className="verified-check" aria-hidden="true">!</span>
                <h2>Target CI did not pass.</h2>
                <p>{run.error ?? "The external action remains unverified. Inspect the target checks before retrying."}</p>
                {run.action_receipt?.external_url && (
                  <a className="download-link" href={run.action_receipt.external_url} target="_blank" rel="noreferrer">Inspect draft pull request <span aria-hidden="true">↗</span></a>
                )}
              </div>
            )}
            {run?.status === "verified" && (
              <div className="verified-result">
                <span className="verified-check" aria-hidden="true">✓</span>
                <h2>{run.action_receipt?.kind === "github_pull_request" ? "Draft PR opened. Read back. Re-run." : "Written. Read back. Re-run."}</h2>
                <p>The destination hash matches the approved payload and all accepted solutions still pass.</p>
                <div className="receipt"><small>ARTIFACT SHA-256</small><code>{shortHash(run.artifact_sha256)}</code></div>
                {run.action_receipt?.external_url ? (
                  <a className="download-link" href={run.action_receipt.external_url} target="_blank" rel="noreferrer">Open draft pull request <span aria-hidden="true">↗</span></a>
                ) : (
                  <a className="download-link" href={`/api/runs/${run.id}/artifact`}>Download verified test <span aria-hidden="true">↓</span></a>
                )}
              </div>
            )}
          </div>

          <div className="trace-block">
            <div className="trace-title">
              <div><span className="section-number">AUDIT TRAIL</span><h2 id="trace-heading">Run evidence</h2></div>
              {run && <span className="live-indicator"><i aria-hidden="true" />{["queued", "analyzing", "applying", "external_ci_pending"].includes(run.status) ? "live" : "persisted"}</span>}
              {run && <a className="evidence-download" href={`/api/runs/${run.id}/evidence`}>Evidence bundle <span aria-hidden="true">↓</span></a>}
            </div>
            {events.length === 0 ? (
              <p className="empty-trace">The signed sequence of analysis, approval, write, and verification will appear here.</p>
            ) : (
              <ol className="event-list">
                {events.map((event) => (
                  <li key={event.id}>
                    <span className="event-node" aria-hidden="true" />
                    <div><strong>{event.message}</strong><small>{event.stage} · {formatTime(event.created_at)}</small></div>
                  </li>
                ))}
              </ol>
            )}
            {run?.analysis && events.length === 0 && (
              <p className="trace-resume">Run {run.id.slice(0, 8)} resumed from durable state.</p>
            )}
          </div>
        </aside>
      </div>

      <footer className="site-footer">
        <span>CourseFuzz / execution is the truth engine</span>
        <span>{run ? `run ${run.id.slice(0, 8)}` : "ready for evidence"}</span>
      </footer>
      <AssignmentImportDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={handleImported}
      />
    </main>
  );
}
