CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    document TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    owner_id TEXT NOT NULL DEFAULT 'local-demo'
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_run_id_id ON events(run_id, id);

CREATE TABLE IF NOT EXISTS approvals (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    payload_sha256 TEXT NOT NULL,
    approval_token TEXT NOT NULL UNIQUE,
    approved_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content BYTEA NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY,
    snapshot_sha256 TEXT NOT NULL UNIQUE,
    document TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS assignments_created_at ON assignments(created_at DESC);

CREATE TABLE IF NOT EXISTS assignment_access (
    assignment_id TEXT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (assignment_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS assignment_access_tenant ON assignment_access(tenant_id, assignment_id);

CREATE INDEX IF NOT EXISTS runs_owner_updated ON runs(owner_id, updated_at DESC);
