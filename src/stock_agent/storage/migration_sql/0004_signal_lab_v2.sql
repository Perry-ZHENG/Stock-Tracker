-- Provenance for generated signal candidates. Source code remains in task-scoped artifacts.
CREATE TABLE IF NOT EXISTS signal_proposals (
    proposal_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_proposals_task_created ON signal_proposals(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS candidate_build_provenance (
    candidate_id TEXT PRIMARY KEY REFERENCES candidate_functions(candidate_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    proposal_id TEXT NOT NULL REFERENCES signal_proposals(proposal_id) ON DELETE RESTRICT,
    build_fingerprint TEXT NOT NULL CHECK (length(build_fingerprint) = 64),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_build_fingerprint
    ON candidate_build_provenance(task_id, build_fingerprint, created_at DESC);
