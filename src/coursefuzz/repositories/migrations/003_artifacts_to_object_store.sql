ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS uri TEXT;
-- We keep 'content' for backwards compatibility during migration, but allow it to be NULL.
ALTER TABLE artifacts ALTER COLUMN content DROP NOT NULL;
