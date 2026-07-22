ALTER TABLE runs ADD COLUMN IF NOT EXISTS leased_by TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS leased_until TIMESTAMP WITH TIME ZONE;

CREATE INDEX IF NOT EXISTS runs_lease_idx ON runs(leased_until) WHERE leased_until IS NOT NULL;

CREATE TABLE IF NOT EXISTS outbox_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS outbox_created_at_idx ON outbox_events(created_at);
