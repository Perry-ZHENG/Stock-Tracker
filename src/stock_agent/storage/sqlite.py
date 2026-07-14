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
)


def open_database(path: Path) -> sqlite3.Connection:
    """Open a runtime database and bring it to the latest verified schema."""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
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


def initialize_runtime_database(
    root: Path,
    config: StockAgentConfig | None = None,
) -> sqlite3.Connection:
    runtime_config = config or validate_config(DEFAULT_CONFIG)
    return initialize_database(root / runtime_config.storage.sqlite_path)


__all__ = ["REQUIRED_TABLES", "initialize_database", "initialize_runtime_database", "open_database"]
