# Deployment

## Production image contract

The Docker image compiles the React client, installs the Python package, runs as UID 10001, and
serves the API and static client from one Uvicorn process on port 8000. `/api/health` exposes the
execution mode, authentication mode, GitHub-destination availability, and deployed commit.

`compose.yaml` is the canonical single-instance deployment shape. It adds a durable `/app/data`
volume, read-only root filesystem, writable 64 MB `/tmp`, all Linux capabilities dropped,
`no-new-privileges`, a 128-process ceiling, and a 1 GB memory ceiling.

For local HTTP verification only:

```powershell
$env:COURSEFUZZ_COOKIE_SECURE = "0"
$env:COURSEFUZZ_ACCESS_KEYS_JSON = '{"reviewer":"replace-with-a-random-24-plus-character-key"}'
docker compose up --build
```

For a public deployment, inject secrets through the hosting platform, keep
`COURSEFUZZ_COOKIE_SECURE=1`, set `COURSEFUZZ_COMMIT_SHA` to the immutable deployed revision (the
app also reads Render's `RENDER_GIT_COMMIT`), mount durable storage at `/app/data`, terminate TLS
before the container, and run exactly one replica. SQLite is intentionally not advertised as
multi-replica infrastructure.

`render.yaml` is the zero-cost public-demo deployment shape: one Free Docker web service and one
Free 1 GB Render Postgres database in Singapore, one web instance, health checks, and deployment
only after GitHub checks pass. Postgres stores assignments, runs, approvals, audit events, and
generated artifact bytes; the web container's filesystem is treated as transient. During the
initial Blueprint setup, provide these secret values:

- `COURSEFUZZ_ACCESS_KEYS_JSON`: for example, a JSON map containing a random 24-plus-character
  judge credential.
- `COURSEFUZZ_JUDGE_ACCESS_TOKEN`: optional independent credential for the fixed `judge-review`
  tenant. It is merged in memory and never rotates or exposes the owner tenant-key map.
- `COURSEFUZZ_GITHUB_TOKEN`: a fine-grained token scoped only to the dedicated demo target.
- `COURSEFUZZ_GITHUB_ALLOWED_REPOS`: the single `owner/repository` demo target (or a tightly
  reviewed comma-separated allowlist).
- `OPENAI_API_KEY`: optional for the live hypothesis provider; blank/unavailable operation uses
  the deterministic bounded fallback and is labelled accordingly.

The server honors the hosting platform's `PORT` environment variable. Render supplies the
deployed Git commit to the health receipt automatically.

The Free web service spins down after 15 idle minutes and can take about a minute to wake. The Free
Postgres database has no backups and expires 30 days after creation. This is acceptable only for
the time-bounded public demonstration; recreate or upgrade the database before expiry, and never
describe this free deployment as production infrastructure.

## Clean-environment smoke gate

1. Build the image from a clean checkout.
2. Start it with a random tenant key and an empty persistent volume.
3. Verify `/api/health` reports the expected commit and `auth: required`.
4. Verify an unauthenticated assignment request returns 401.
5. Sign in through the browser, run the seeded golden path, approve the exact payload, apply it,
   download the read-back artifact, reload the run URL, and confirm the audit trail persists.
6. Restart the container with the same volume and confirm the run is still available.
7. Repeat from a clean, logged-out phone-sized browser.

GitHub delivery needs a dedicated integration repository plus a fine-grained token limited to
`Contents: write` and `Pull requests: write`. The release proof must show the exact base commit,
run-specific branch, draft pull request, destination read-back SHA-256, and rerun receipt. Do not
use the product repository itself as a destructive demo target. Set the server-side repository
allowlist even when the token itself is already repository-scoped. The target repository must
provide `solution.py` with the assignment entrypoint so its CI can execute the generated pytest.

For multi-workspace Round-2 deployments, replace the static token with all three GitHub App values:

- `COURSEFUZZ_GITHUB_APP_ID` — the numeric App ID;
- `COURSEFUZZ_GITHUB_APP_PRIVATE_KEY` — the PEM private key (literal `\n` escapes are accepted);
- `COURSEFUZZ_GITHUB_INSTALLATIONS_JSON` — tenant IDs mapped to exact repository/installation-ID
  maps, for example `{"course-a":{"owner/autograder":12345}}`.

The App configuration is fail-closed: supplying only one or two values prevents startup. When App
mode is active, `/api/health` reports `github_auth: github-app`; it never returns installation IDs,
tokens, or private-key material. The static beta path reports `static-token`.

### Self-serve repository onboarding (signed installation callbacks)

Instead of an operator hand-editing `COURSEFUZZ_GITHUB_INSTALLATIONS_JSON`, a workspace can onboard
its own repository by installing the CourseFuzz GitHub App. Set the App's webhook secret as
`COURSEFUZZ_GITHUB_WEBHOOK_SECRET` and point the App's webhook URL at `POST /api/github/webhook`.

- The endpoint verifies the `X-Hub-Signature-256` HMAC before doing anything (fail-closed on a
  missing secret, missing store, or bad signature), deduplicates by `X-GitHub-Delivery` so a
  redelivery never applies twice, and persists `installation` / `installation_repositories` events
  into the durable store (the same SQLite/Postgres database as runs). Created and unsuspend events
  replace the installation's repository set; added/removed adjust it; suspend and deleted revoke it.
- `GET /api/github/repositories` (authenticated) returns the repositories the caller's workspace has
  onboarded — the data a repository picker renders. It is read-only and tenant-scoped.
- Token minting is dynamic: `StoredGitHubCredentialProvider` layers over the static map, so a tenant
  may write only to repositories inside the installation it has bound, and only while that
  installation is active. The static demo/judge map keeps working unchanged underneath.

Binding a workspace to an installation over `POST /api/github/installations/claim` (raw, unverified)
is **disabled by default** (`COURSEFUZZ_ENABLE_SELF_SERVE_CLAIM=0`) because binding by raw
installation ID is guessable. When enabled in a controlled environment, claiming is
first-claim-wins: an installation already owned by another workspace cannot be re-claimed.

The safe, always-on binding path is **GitHub OAuth identity verification**. Register a GitHub OAuth
app (or the App's user-authorization flow) and set `COURSEFUZZ_GITHUB_OAUTH_CLIENT_ID`,
`COURSEFUZZ_GITHUB_OAUTH_CLIENT_SECRET`, and `COURSEFUZZ_GITHUB_OAUTH_REDIRECT_URI` (ending in
`/api/github/callback`). Then:

- `GET /api/github/login?installation_id=<id>` (authenticated) redirects the workspace to GitHub,
  carrying the tenant and target installation in a `state` value signed with the OAuth client secret
  (the SameSite-strict session cookie does not survive the return redirect, so the signed state — not
  the cookie — carries identity back).
- `GET /api/github/callback` exchanges the code for a user token, reads the user's own
  `/user/installations`, and binds the workspace **only if** the installation ID from the signed
  state is one the user actually owns and it exists in the store. The browser is redirected to
  `/?github=connected`, `denied`, `conflict`, or `error`. No feature flag is needed because ownership
  is proven, not asserted.

## Current evidence and blocker

The public demo is live at <https://coursefuzz.onrender.com>. On 2026-07-22, the logged-out browser
gate rendered successfully and `/api/health` returned HTTP 200 with `storage: postgres`,
`auth: required`, `github_destination: configured`, and deployed commit
`5e1643493e7e9c68a45a1a8e7d0a6fd5264576c4`. The matching
[main CI](https://github.com/bansalbhunesh/CourseFuzz/actions/runs/29886596892) passed the backend,
frontend, production-container, frozen-benchmark, and live runc/runsc isolation jobs.

On 2026-07-22 the deployment was moved off the earlier shared personal access token onto the
least-privilege GitHub App. Production now mints short-lived, repository-scoped installation tokens
(the App installation is limited to the demo target with `Checks: read` plus `Contents` and
`Pull requests: write`), refreshed before expiry and never persisted in a run or assignment
document. `/api/health` reports `github_auth: github-app` in place of `static-token`, confirming the
App path is active in the live container; the exposed shared token is being retired. This is the
credential path multi-workspace users and Round-2 judges exercise.

This is deployment evidence, not final video closure. The release manifest is now
`round-2-active`: the CourseFuzz-created [Demo Target draft PR
#3](https://github.com/bansalbhunesh/CourseFuzz-Demo-Target/pull/3) preserves the external write,
byte read-back, and passing target-CI receipt. The final public demo video is still required before
the manifest can become `submission-ready`.

The restricted AST runner remains a demonstration boundary even inside this container. A public
service that accepts hostile submissions still requires one no-network microVM or hardened
container per execution, not merely the API container controls above.
