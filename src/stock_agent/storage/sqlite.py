"""SQLite initialization for online runtime state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stock_agent.config import DEFAULT_CONFIG, StockAgentConfig, validate_config

REQUIRED_TABLES = (
    "signals",
    "trace_chain",
    "health_metrics",
    "checkpoints",
    "config_changes",
    "notifications",
    "news_items",
)


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: Path) -> sqlite3.Connection:
    connection = open_database(path)
    _create_tables(connection)
    return connection


def initialize_runtime_database(
    root: Path,
    config: StockAgentConfig | None = None,
) -> sqlite3.Connection:
    runtime_config = config or validate_config(DEFAULT_CONFIG)
    sqlite_path = root / runtime_config.storage.sqlite_path
    return initialize_database(sqlite_path)


def _create_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            strategy_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('buy_watch', 'sell_watch', 'observe')),
            strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
            confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            reason TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            source_bar_ids TEXT NOT NULL,
            data_quality TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trace_chain (
            trace_id TEXT PRIMARY KEY,
            parent_id TEXT,
            module TEXT NOT NULL,
            input_ref TEXT NOT NULL,
            output_ref TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success', 'skipped', 'failed')),
            error_msg TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS health_metrics (
            metric_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            module TEXT NOT NULL,
            heartbeat_at TEXT,
            data_latency_sec REAL NOT NULL CHECK (data_latency_sec >= 0),
            error_rate REAL NOT NULL CHECK (error_rate >= 0 AND error_rate <= 1),
            consecutive_failures INTEGER NOT NULL CHECK (consecutive_failures >= 0),
            alert_failures INTEGER NOT NULL CHECK (alert_failures >= 0),
            status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'unhealthy')),
            details TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            module TEXT NOT NULL,
            checkpoint_key TEXT NOT NULL,
            checkpoint_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config_changes (
            change_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            before_config TEXT,
            after_config TEXT,
            diff TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            notification_id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
            error_msg TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS news_items (
            news_id TEXT PRIMARY KEY,
            symbol TEXT,
            market TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            url TEXT NOT NULL,
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            retention_level TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
