CREATE TABLE research_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_fingerprint TEXT,
    last_report_id TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE research_subscription_runs (
    run_id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES research_subscriptions(subscription_id) ON DELETE CASCADE,
    scheduled_for TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    task_id TEXT,
    fingerprint TEXT,
    previous_report_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_research_subscriptions_due ON research_subscriptions(status, next_run_at, lease_until);
