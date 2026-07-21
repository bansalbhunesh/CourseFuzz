import { useEffect, useRef, useState } from "react";

type RunStatus =
  | "queued"
  | "analyzing"
  | "approval_required"
  | "approved"
  | "applying"
  | "verified"
  | "failed";

type Demo = {
  id: string;
  title: string;
  summary: string;
  language: string;
  entrypoint: string;
  instructor_tests: Array<{ inputs: number[]; expected: string; label: string }>;
  mutant_count: number;
  accepted_solution_count: number;
  mode: "live-gpt-5.6" | "deterministic-fallback";
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
  expected: string | null;
  actual: string | null;
  killed_mutants: string[];
};

type Analysis = {
  before: Metrics;
  projected_after: Metrics;
  survivors_before: string[];
  hypothesis_verdicts: Verdict[];
  candidate: {
    id: string;
    test: { inputs: number[]; expected: string; label: string; source: string };
    observed_actual: string | null;
    rationale: string;
    target_mutants: string[];
    payload_sha256: string;
    pytest_source: string;
  };
  evidence: Record<string, unknown>;
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
  verified: 4,
  failed: 4,
};

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? "The request failed.");
  }
  return response.json() as Promise<T>;
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

export function App() {
  const [demo, setDemo] = useState<Demo | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [receipt, setReceipt] = useState<ApprovalReceipt | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let active = true;
    api<Demo>("/api/demo")
      .then((value) => active && setDemo(value))
      .catch((reason: Error) => active && setError(reason.message));

    const savedRun = new URLSearchParams(window.location.search).get("run");
    if (savedRun) {
      api<Run>(`/api/runs/${savedRun}`)
        .then((value) => active && setRun(value))
        .catch(() => {
          window.history.replaceState({}, "", window.location.pathname);
        });
    }
    return () => {
      active = false;
    };
  }, []);

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
      "analysis.started",
      "analysis.hypotheses",
      "analysis.verified",
      "approval.required",
      "approval.granted",
      "patch.applying",
      "patch.verified",
      "patch.failed",
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

  const activeStep = run ? statusOrder[run.status] : -1;

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
      window.history.replaceState({}, "", `?run=${value.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the run.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!run?.analysis || !reviewed) return;
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

  if (!demo && !error) {
    return <main className="center-state" aria-live="polite">Loading the seeded assignment…</main>;
  }

  return (
    <main className="app-shell">
      <header className="masthead">
        <a className="wordmark" href="/" aria-label="CourseFuzz home">
          <span className="wordmark-mark" aria-hidden="true">CF</span>
          <span>CourseFuzz</span>
        </a>
        <div className="run-meta">
          <span className="mode-dot" aria-hidden="true" />
          <span>{demo?.mode === "live-gpt-5.6" ? "GPT-5.6 hypotheses" : "Deterministic fallback"}</span>
          <span className="meta-separator" aria-hidden="true">/</span>
          <span>seeded demo corpus</span>
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
        <h1>{run?.analysis ? "One wrong solution still passes." : "Your tests pass. A wrong solution does too."}</h1>
        <p>{run?.analysis ? run.analysis.candidate.rationale : demo?.summary}</p>
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
                    <span>→ {test.expected}</span>
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
                  <span>{run.analysis.before.surviving_mutants} survivors before</span>
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
                    <div><small>REFERENCE</small><strong>{run.analysis.candidate.test.expected}</strong></div>
                    <div className="wrong-output"><small>WRONG PROGRAM</small><strong>{run.analysis.candidate.observed_actual ?? "not captured"}</strong></div>
                  </div>
                  <p>Execution reproduced the disagreement after minimizing the generated hypothesis.</p>
                  <footer>
                    <span>Execution-backed</span>
                    <span>{run.analysis.candidate.target_mutants.length} mutant{run.analysis.candidate.target_mutants.length === 1 ? "" : "s"} killed</span>
                    <span>source: {run.analysis.candidate.test.source}</span>
                  </footer>
                </article>
              )}

              <section className="patch-proof" aria-labelledby="patch-heading">
                <div className="section-heading compact">
                  <div><span className="section-number">PROPOSED PATCH</span><h3 id="patch-heading">One test closes the gap</h3></div>
                  <span className="hash-label">SHA {shortHash(run.analysis.candidate.payload_sha256)}</span>
                </div>
                <pre><code>{run.analysis.candidate.pytest_source}</code></pre>
                <dl className="evidence-notes">
                  <div><dt>Scope</dt><dd>One generated pytest</dd></div>
                  <div><dt>Control check</dt><dd>{pct(run.analysis.projected_after.accepted_solution_pass_rate)} accepted solutions pass</dd></div>
                  <div><dt>Write target</dt><dd>verified_tests/test_generated.py</dd></div>
                </dl>
              </section>
            </>
          )}
        </section>

        <aside className="action-column" aria-labelledby="trace-heading">
          <div className="action-block">
            <span className="section-number">ACTION BOUNDARY</span>
            {!run?.analysis && (
              <><h2>No write without proof.</h2><p>CourseFuzz can inspect and execute freely. Writing a generated test requires review of the exact payload.</p></>
            )}
            {run?.analysis && run.status === "approval_required" && (
              <>
                <h2>Approve this exact test?</h2>
                <p>The approval token will be bound to this payload hash. Any content change invalidates it.</p>
                <label className="review-check">
                  <input type="checkbox" checked={reviewed} onChange={(event) => setReviewed(event.target.checked)} />
                  <span>I reviewed the input, expected output, and source above.</span>
                </label>
                <button className="primary-action full" type="button" disabled={!reviewed || busy} onClick={approve}>
                  {busy ? "Binding approval…" : "Approve exact payload"}<span aria-hidden="true">→</span>
                </button>
              </>
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
            {run?.status === "applying" && (
              <><h2>Reading back the destination…</h2><p>The result is not complete until the written bytes and full rerun agree.</p></>
            )}
            {run?.status === "verified" && (
              <div className="verified-result">
                <span className="verified-check" aria-hidden="true">✓</span>
                <h2>Written. Read back. Re-run.</h2>
                <p>The destination hash matches the approved payload and all accepted solutions still pass.</p>
                <div className="receipt"><small>ARTIFACT SHA-256</small><code>{shortHash(run.artifact_sha256)}</code></div>
                <a className="download-link" href={`/api/runs/${run.id}/artifact`}>Download verified test <span aria-hidden="true">↓</span></a>
              </div>
            )}
          </div>

          <div className="trace-block">
            <div className="trace-title">
              <div><span className="section-number">AUDIT TRAIL</span><h2 id="trace-heading">Run evidence</h2></div>
              {run && <span className="live-indicator"><i aria-hidden="true" />{["queued", "analyzing", "applying"].includes(run.status) ? "live" : "persisted"}</span>}
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
    </main>
  );
}
