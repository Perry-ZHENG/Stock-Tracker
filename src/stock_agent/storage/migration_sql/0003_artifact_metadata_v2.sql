-- Artifact source is required to bind EvidenceRef.source to persisted metadata.
ALTER TABLE artifacts ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_artifacts_task_source ON artifacts(task_id, source);
