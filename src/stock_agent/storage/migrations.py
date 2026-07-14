"""Versioned SQLite migration runner for the V2 research domain."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MIGRATION_DIRECTORY = Path(__file__).with_name("migration_sql")


class MigrationError(RuntimeError):
    """Base error for a failed or inconsistent schema migration."""


class MigrationChecksumError(MigrationError):
    """Raised when an already applied migration has been edited or removed."""


class MigrationOrderError(MigrationError):
    """Raised when migration files do not form a contiguous version sequence."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def apply_migrations(connection: sqlite3.Connection, *, migration_dir: Path | None = None) -> None:
    """Apply every pending migration in order and verify applied checksums."""

    migrations = load_migrations(migration_dir)
    _ensure_migration_table(connection)
    applied = _applied_checksums(connection)

    missing_applied = set(applied) - {migration.version for migration in migrations}
    if missing_applied:
        raise MigrationChecksumError(f"applied migration files are missing: {sorted(missing_applied)}")

    for migration in migrations:
        existing_checksum = applied.get(migration.version)
        if existing_checksum is not None:
            if existing_checksum != migration.checksum:
                raise MigrationChecksumError(f"migration {migration.version:04d} checksum does not match")
            continue
        _apply_migration(connection, migration)


def load_migrations(migration_dir: Path | None = None) -> list[Migration]:
    """Read SQL migrations and reject missing, duplicate, or malformed versions."""

    directory = migration_dir or MIGRATION_DIRECTORY
    paths = sorted(directory.glob("*.sql"))
    migrations: list[Migration] = []
    for path in paths:
        prefix, separator, suffix = path.name.partition("_")
        if not separator or not prefix.isdigit() or len(prefix) != 4 or not suffix.endswith(".sql"):
            raise MigrationOrderError(f"invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"migration {path.name} is empty")
        migrations.append(
            Migration(
                version=int(prefix),
                name=path.name,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )

    versions = [migration.version for migration in migrations]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationOrderError(f"migration versions must be contiguous from 0001; found {versions}")
    return migrations


def applied_migrations(connection: sqlite3.Connection) -> list[dict[str, str | int]]:
    """Return migration audit rows in version order for diagnostics and tests."""

    _ensure_migration_table(connection)
    rows = connection.execute(
        "SELECT version, name, applied_at, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return [dict(row) for row in rows]


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _applied_checksums(connection: sqlite3.Connection) -> dict[int, str]:
    rows = connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def _apply_migration(connection: sqlite3.Connection, migration: Migration) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in _split_sql_statements(migration.sql):
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at, checksum)
            VALUES (?, ?, ?, ?)
            """,
            (
                migration.version,
                migration.name,
                datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                migration.checksum,
            ),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise MigrationError(f"migration {migration.name} failed and was rolled back") from exc


def _split_sql_statements(sql: str) -> list[str]:
    """Split project migration SQL while respecting quoted SQL string literals."""

    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            buffer.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    buffer.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            buffer.append(character)
        elif character == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
        else:
            buffer.append(character)
        index += 1
    statement = "".join(buffer).strip()
    if statement:
        statements.append(statement)
    return statements


__all__ = [
    "Migration",
    "MigrationChecksumError",
    "MigrationError",
    "MigrationOrderError",
    "applied_migrations",
    "apply_migrations",
    "load_migrations",
]
