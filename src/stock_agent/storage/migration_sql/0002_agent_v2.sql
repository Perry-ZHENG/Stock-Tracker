-- V2 research task, evidence, signal lifecycle, analysis, report, and approval state.
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'paused', 'cancelled', 'failed', 'completed')),
    budget_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_updated ON agent_tasks(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_plans (
    plan_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, revision)
);

CREATE TABLE IF NOT EXISTS agent_steps (
    step_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES agent_plans(plan_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    actor TEXT NOT NULL CHECK (actor IN ('orchestrator', 'signal_discovery', 'anomaly_analysis', 'macro_analysis', 'report')),
    depends_on_json TEXT NOT NULL,
    input_refs_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    claimed_by TEXT,
    claimed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_claim ON agent_steps(task_id, status, plan_id);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    summary TEXT NOT NULL,
    artifact_refs_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_task_created ON agent_messages(task_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('bars', 'news_body', 'model_response', 'candidate_source', 'validation_metrics', 'report', 'analysis')),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    storage_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(task_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_expiry ON artifacts(expires_at);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN ('bar', 'news', 'signal', 'trace', 'provider', 'mcp', 'analysis')),
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT,
    trust_level TEXT NOT NULL CHECK (trust_level IN ('low', 'medium', 'high'))
);

CREATE INDEX IF NOT EXISTS idx_evidence_task_observed ON evidence(task_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS signal_definitions (
    signal_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    feature_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'validating', 'validated', 'active', 'suspended', 'retired')),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_functions (
    candidate_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    interface_version TEXT NOT NULL,
    source_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    dependencies_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_validations (
    validation_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate_functions(candidate_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('pass', 'revise', 'reject')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_validations_candidate ON signal_validations(candidate_id, created_at DESC);

CREATE TABLE IF NOT EXISTS signal_versions (
    signal_id TEXT NOT NULL REFERENCES signal_definitions(signal_id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL CHECK (status IN ('draft', 'validating', 'validated', 'active', 'suspended', 'retired')),
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    validation_id TEXT NOT NULL REFERENCES signal_validations(validation_id) ON DELETE RESTRICT,
    approved_by TEXT,
    approved_at TEXT,
    PRIMARY KEY(signal_id, version)
);

CREATE INDEX IF NOT EXISTS idx_signal_versions_status ON signal_versions(status, signal_id);

CREATE TABLE IF NOT EXISTS signal_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('positive', 'negative', 'neutral', 'uncertain')),
    strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reason TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    FOREIGN KEY(signal_id, version) REFERENCES signal_versions(signal_id, version) ON DELETE RESTRICT,
    UNIQUE(signal_id, version, symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_signal_observations_symbol_timestamp ON signal_observations(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    analysis_type TEXT NOT NULL CHECK (analysis_type IN ('anomaly', 'macro')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analyses_task_type ON analyses(task_id, analysis_type, created_at DESC);

CREATE TABLE IF NOT EXISTS report_drafts (
    draft_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_drafts_task_generated ON report_drafts(task_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS final_reports (
    report_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE REFERENCES report_drafts(draft_id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_final_reports_task_published ON final_reports(task_id, published_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('signal_version', 'report')),
    subject_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by TEXT NOT NULL,
    reason TEXT,
    decided_at TEXT NOT NULL,
    UNIQUE(subject_type, subject_id, decided_by)
);

CREATE INDEX IF NOT EXISTS idx_approvals_subject ON approvals(subject_type, subject_id, decided_at DESC);
