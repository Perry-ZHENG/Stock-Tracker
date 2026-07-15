"""SQLite connection setup and versioned runtime schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stock_agent.config import DEFAULT_CONFIG, StockAgentConfig, validate_config
from stock_agent.storage.migrations import apply_migrations

REQUIRED_TABLES = (
    "schema_migrations",
    "signals",
    "trace_chain",
    "health_metrics",
    "checkpoints",
    "config_changes",
    "notifications",
    "news_items",
    "signal_statistics",
    "strategy_snapshots",
    "security_audit",
    "abnormal_bars",
    "agent_runs",
    "input_control_state",
    "input_interface_presence",
    "input_switch_requests",
    "agent_tasks",
    "agent_plans",
    "agent_steps",
    "agent_messages",
    "artifacts",
    "evidence",
    "signal_definitions",
    "candidate_functions",
    "signal_validations",
    "signal_versions",
    "signal_observations",
    "analyses",
    "report_drafts",
    "final_reports",
    "approvals",
    "signal_proposals",
    "candidate_build_provenance",
    "agent_step_payloads",
    "research_subscriptions",
    "research_subscription_runs",
    "agent_trace_events",
    "agent_budget_snapshots",
    "provider_credit_reservations",
)


def open_database(path: Path) -> sqlite3.Connection:
    """Open a runtime database and bring it to the latest verified schema."""

    path.parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may execute synchronous entry adapters on worker threads.  The
    # service boundary serializes shared lifecycle work with an RLock, while
    # SQLite still provides process-level locking through WAL and busy_timeout.
    connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        apply_migrations(connection)
    except Exception:
        connection.close()
        raise
    return connection


def initialize_database(path: Path) -> sqlite3.Connection:
    """Compatibility entry point for callers that initialize a runtime database."""

    return open_database(path)


def _create_tables(connection: sqlite3.Connection) -> None:
    """Legacy in-memory test adapter; production initialization uses migrations."""

    apply_migrations(connection)


def initialize_runtime_database(
    root: Path,
    config: StockAgentConfig | None = None,
) -> sqlite3.Connection:
    runtime_config = config or validate_config(DEFAULT_CONFIG)
    return initialize_database(root / runtime_config.storage.sqlite_path)


__all__ = ["REQUIRED_TABLES", "initialize_database", "initialize_runtime_database", "open_database"]
