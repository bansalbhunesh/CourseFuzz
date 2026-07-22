import { useEffect, useState } from "react";

type RepositoriesResponse = {
  installation_id: number | null;
  repositories: string[];
};

type ConnectStatus = "connected" | "denied" | "conflict" | "error" | null;

const STATUS_MESSAGE: Record<
  Exclude<ConnectStatus, null>,
  { tone: "ok" | "error"; text: string }
> = {
  connected: {
    tone: "ok",
    text: "Repository connected. It is now available as a verified write destination.",
  },
  denied: {
    tone: "error",
    text: "That installation is not owned by your GitHub account, so it was not connected.",
  },
  conflict: {
    tone: "error",
    text: "That installation is already connected to another workspace.",
  },
  error: {
    tone: "error",
    text: "GitHub verification did not complete. Please try connecting again.",
  },
};

function readStatus(): ConnectStatus {
  const value = new URLSearchParams(window.location.search).get("github");
  if (value === "connected" || value === "denied" || value === "conflict" || value === "error") {
    return value;
  }
  return null;
}

function readPendingInstallationId(): number | null {
  const raw = new URLSearchParams(window.location.search).get("installation_id");
  if (!raw) return null;
  const parsed = Number.parseInt(raw, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Self-serve GitHub repository picker. Shows the repositories the workspace onboarded via the App
 * (GET /api/github/repositories) and, when GitHub returns the user here after installing the App
 * (?installation_id=…), offers the OAuth-verified connect link. Renders nothing unless the
 * deployment is in GitHub App mode.
 */
export function GitHubConnectPanel({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<RepositoriesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const status = readStatus();
  const pendingInstallationId = readPendingInstallationId();

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    fetch("/api/github/repositories", { credentials: "same-origin" })
      .then((response) => (response.ok ? (response.json() as Promise<RepositoriesResponse>) : null))
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  if (!enabled) return null;

  const repositories = data?.repositories ?? [];
  const installationId = data?.installation_id ?? null;
  const showConnect = pendingInstallationId !== null && installationId !== pendingInstallationId;

  return (
    <section className="github-panel" aria-labelledby="github-panel-heading">
      <div className="section-heading">
        <div>
          <span className="section-number">GITHUB</span>
          <h2 id="github-panel-heading">Connected repositories</h2>
        </div>
      </div>
      {status && (
        <p className={`github-status github-status-${STATUS_MESSAGE[status].tone}`} role="status">
          {STATUS_MESSAGE[status].text}
        </p>
      )}
      {loading && <p className="github-muted">Loading your onboarded repositories…</p>}
      {!loading && repositories.length > 0 && (
        <ul className="github-repo-list">
          {repositories.map((repository) => (
            <li key={repository}>
              <code>{repository}</code>
            </li>
          ))}
        </ul>
      )}
      {!loading && repositories.length === 0 && !showConnect && (
        <p className="github-muted">
          No repository is connected yet. Install the CourseFuzz GitHub App on your autograder
          repository; GitHub returns you here to verify and connect it.
        </p>
      )}
      {showConnect && (
        <a className="primary-action" href={`/api/github/login?installation_id=${pendingInstallationId}`}>
          Verify &amp; connect installation #{pendingInstallationId}
          <span aria-hidden="true">→</span>
        </a>
      )}
    </section>
  );
}
