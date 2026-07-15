CREATE TABLE agent_trace_events (
    trace_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    plan_id TEXT,
    step_id TEXT,
    parent_trace_id TEXT,
    component TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    error_category TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    input_ref_json TEXT NOT NULL,
    output_ref_json TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_agent_trace_events_task_created
ON agent_trace_events(task_id, created_at, trace_id);

CREATE TABLE agent_budget_snapshots (
    task_id TEXT PRIMARY KEY REFERENCES agent_tasks(task_id) ON DELETE CASCADE,
    max_model_calls INTEGER NOT NULL CHECK (max_model_calls >= 0),
    max_tool_calls INTEGER NOT NULL CHECK (max_tool_calls >= 0),
    used_model_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_model_calls >= 0),
    used_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_tool_calls >= 0),
    input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    estimated_cost_usd REAL NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
    sandbox_cpu_ms INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_cpu_ms >= 0),
    sandbox_memory_mb_ms INTEGER NOT NULL DEFAULT 0 CHECK (sandbox_memory_mb_ms >= 0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TEXT NOT NULL
);
