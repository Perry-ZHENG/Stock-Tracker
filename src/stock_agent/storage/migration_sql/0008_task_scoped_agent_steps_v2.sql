-- Step names are stable plan vocabulary, not globally unique database IDs.
-- Preserve existing task state while making every step and payload task-scoped.
PRAGMA defer_foreign_keys = ON;

CREATE TABLE agent_steps_v2 (
    step_id TEXT NOT NULL,
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
    updated_at TEXT NOT NULL,
    PRIMARY KEY(task_id, step_id)
);

INSERT INTO agent_steps_v2 (
    step_id, plan_id, task_id, actor, depends_on_json, input_refs_json,
    status, attempt, max_attempts, claimed_by, claimed_at, updated_at
)
SELECT
    step_id, plan_id, task_id, actor, depends_on_json, input_refs_json,
    status, attempt, max_attempts, claimed_by, claimed_at, updated_at
FROM agent_steps;

CREATE TABLE agent_step_payloads_v2 (
    step_id TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    input_json TEXT NOT NULL,
    output_artifact_id TEXT REFERENCES artifacts(artifact_id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(task_id, step_id),
    FOREIGN KEY(task_id, step_id) REFERENCES agent_steps_v2(task_id, step_id) ON DELETE CASCADE
);

INSERT INTO agent_step_payloads_v2 (step_id, task_id, input_json, output_artifact_id, updated_at)
SELECT step_id, task_id, input_json, output_artifact_id, updated_at
FROM agent_step_payloads;

DROP TABLE agent_step_payloads;
DROP TABLE agent_steps;
ALTER TABLE agent_steps_v2 RENAME TO agent_steps;
ALTER TABLE agent_step_payloads_v2 RENAME TO agent_step_payloads;

CREATE INDEX idx_agent_steps_claim ON agent_steps(task_id, status, plan_id);
CREATE INDEX idx_agent_step_payloads_task ON agent_step_payloads(task_id, updated_at);
