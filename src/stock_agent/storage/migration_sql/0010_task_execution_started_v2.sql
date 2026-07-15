-- Start duration budgets when a Worker actually begins a task, not when a user submits it.
ALTER TABLE agent_tasks ADD COLUMN execution_started_at TEXT;
