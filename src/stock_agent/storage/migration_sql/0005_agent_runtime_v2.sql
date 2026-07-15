CREATE TABLE agent_step_payloads (
    step_id TEXT PRIMARY KEY REFERENCES agent_steps(step_id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    input_json TEXT NOT NULL,
    output_artifact_id TEXT REFERENCES artifacts(artifact_id),
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_agent_step_payloads_task ON agent_step_payloads(task_id, updated_at);
