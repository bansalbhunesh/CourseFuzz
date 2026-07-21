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

`render.yaml` is the public-demo deployment shape: a paid Starter Docker service in Singapore,
one 1 GB persistent disk, one instance, health checks, and deployment only after GitHub checks
pass. During the initial Blueprint setup, provide these secret values:

- `COURSEFUZZ_ACCESS_KEYS_JSON`: for example, a JSON map containing a random 24-plus-character
  judge credential.
- `COURSEFUZZ_GITHUB_TOKEN`: a fine-grained token scoped only to the dedicated demo target.
- `COURSEFUZZ_GITHUB_ALLOWED_REPOS`: the single `owner/repository` demo target (or a tightly
  reviewed comma-separated allowlist).
- `OPENAI_API_KEY`: optional for the live hypothesis provider; blank/unavailable operation uses
  the deterministic bounded fallback and is labelled accordingly.

The server honors the hosting platform's `PORT` environment variable. Render supplies the
deployed Git commit to the health receipt automatically.

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
allowlist even when the token itself is already repository-scoped.

## Current evidence and blocker

CI builds the production image and smoke-tests the health endpoint and compiled client. The local
API and browser golden paths are verified. A public URL, persistent hosted volume, and live GitHub
draft-PR receipt do not exist yet, so CourseFuzz is not submission-ready or production-ready.

The restricted AST runner remains a demonstration boundary even inside this container. A public
service that accepts hostile submissions still requires one no-network microVM or hardened
container per execution, not merely the API container controls above.
