from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from stock_agent.storage.migrations import (
    MigrationChecksumError,
    MigrationError,
    MigrationOrderError,
    applied_migrations,
    apply_migrations,
)
from stock_agent.storage.sqlite import REQUIRED_TABLES, initialize_database


def test_empty_database_applies_all_migrations_idempotently(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite"
    connection = initialize_database(database_path)

    first = applied_migrations(connection)
    table_names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    connection.close()

    second_connection = initialize_database(database_path)
    second = applied_migrations(second_connection)
    second_connection.close()

    assert [row["version"] for row in first] == [1, 2, 3]
    assert first == second
    assert set(REQUIRED_TABLES).issubset(table_names)


def test_legacy_database_is_upgraded_without_losing_existing_signal_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite"
    legacy = sqlite3.connect(database_path)
    legacy.execute(
        """
        CREATE TABLE signals (
            signal_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL, direction TEXT NOT NULL, strength REAL NOT NULL,
            confidence REAL NOT NULL, reason TEXT NOT NULL, trace_id TEXT NOT NULL,
            source_bar_ids TEXT NOT NULL, data_quality TEXT NOT NULL, created_at TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO signals VALUES (
            'legacy-signal', 'ma_cross', 'QQQ', '2026-07-01T00:00:00Z', 'observe',
            0.5, 0.5, 'legacy', 'trace-legacy', '[]', 'normal', '2026-07-01T00:00:00Z'
        )
        """
    )
    legacy.commit()
    legacy.close()

    connection = initialize_database(database_path)
    count = connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    migrations = applied_migrations(connection)
    connection.close()

    assert count == 1
    assert [row["version"] for row in migrations] == [1, 2, 3]


def test_changed_applied_migration_checksum_is_rejected(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    migration = migration_dir / "0001_baseline.sql"
    migration.write_text("CREATE TABLE checksum_probe (id INTEGER PRIMARY KEY);", encoding="utf-8")
    connection = _connection(tmp_path / "checksum.sqlite")

    apply_migrations(connection, migration_dir=migration_dir)
    migration.write_text("CREATE TABLE checksum_probe (id INTEGER PRIMARY KEY, name TEXT);", encoding="utf-8")

    with pytest.raises(MigrationChecksumError):
        apply_migrations(connection, migration_dir=migration_dir)
    connection.close()


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0001_broken.sql").write_text(
        "CREATE TABLE partial_probe (id INTEGER PRIMARY KEY); INVALID SQL;",
        encoding="utf-8",
    )
    connection = _connection(tmp_path / "rollback.sqlite")

    with pytest.raises(MigrationError):
        apply_migrations(connection, migration_dir=migration_dir)

    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'partial_probe'"
    ).fetchone() is None
    assert applied_migrations(connection) == []
    connection.close()


def test_migration_versions_must_start_at_one_and_be_contiguous(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0002_orphan.sql").write_text("CREATE TABLE orphan_probe (id INTEGER);", encoding="utf-8")
    connection = _connection(tmp_path / "order.sqlite")

    with pytest.raises(MigrationOrderError):
        apply_migrations(connection, migration_dir=migration_dir)
    connection.close()


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
