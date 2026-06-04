"""Configuration review state machine."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from stock_agent.config import render_config_yaml, validate_config
from stock_agent.storage.repositories import (
    get_config_change,
    insert_config_change,
    update_config_change_status,
)

ConfigChangeStatus = Literal[
    "draft",
    "pending_review",
    "approved",
    "rejected",
    "applied",
    "rollback",
    "failed",
]

VALID_CONFIG_CHANGE_STATUSES: tuple[ConfigChangeStatus, ...] = (
    "draft",
    "pending_review",
    "approved",
    "rejected",
    "applied",
    "rollback",
    "failed",
)


class ConfigChangeError(ValueError):
    """Raised when a config change cannot move through the review state machine."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_config_change(
    connection: sqlite3.Connection,
    *,
    change_id: str,
    source: str,
    before_config: dict[str, Any],
    after_config: dict[str, Any],
    diff: str,
    status: ConfigChangeStatus = "pending_review",
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in VALID_CONFIG_CHANGE_STATUSES:
        raise ConfigChangeError(f"invalid config change status: {status}")
    if source == "telegram" and status != "pending_review":
        raise ConfigChangeError("telegram changes must enter pending_review and cannot apply directly")

    timestamp = now or utc_now()
    insert_config_change(
        connection,
        change_id=change_id,
        status=status,
        source=source,
        before_config=before_config,
        after_config=after_config,
        diff=diff,
        created_at=timestamp,
        updated_at=timestamp,
    )
    change = get_config_change(connection, change_id)
    assert change is not None
    return change


def reject_config_change(
    connection: sqlite3.Connection,
    *,
    change_id: str,
    reason: str = "rejected by CLI",
    now: datetime | None = None,
) -> dict[str, Any]:
    change = _require_change(connection, change_id)
    if change["status"] != "pending_review":
        raise ConfigChangeError(f"only pending_review changes can be rejected; got {change['status']}")
    update_config_change_status(
        connection,
        change_id=change_id,
        status="rejected",
        diff=f"{change['diff']}\nREJECTED: {reason}",
        updated_at=now or utc_now(),
    )
    return _require_change(connection, change_id)


def approve_config_change(
    connection: sqlite3.Connection,
    *,
    change_id: str,
    config_path: Path,
    reload_validator: Callable[[dict[str, Any]], object] = validate_config,
    now: datetime | None = None,
) -> dict[str, Any]:
    change = _require_change(connection, change_id)
    if change["status"] != "pending_review":
        raise ConfigChangeError(f"only pending_review changes can be approved; got {change['status']}")

    timestamp = now or utc_now()
    update_config_change_status(
        connection,
        change_id=change_id,
        status="approved",
        updated_at=timestamp,
    )

    before_text = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    try:
        after_config = change["after_config"]
        if not isinstance(after_config, dict):
            raise ConfigChangeError("after_config must be a config object")
        validate_config(after_config)
        new_text = render_config_yaml(after_config)
        _atomic_write(config_path, new_text)
        reload_validator(after_config)
    except Exception as exc:
        if before_text is not None:
            _atomic_write(config_path, before_text)
            status: ConfigChangeStatus = "rollback"
        else:
            if config_path.exists():
                config_path.unlink()
            status = "failed"
        update_config_change_status(
            connection,
            change_id=change_id,
            status=status,
            diff=f"{change['diff']}\nAPPLY_FAILED: {exc}",
            updated_at=utc_now(),
        )
        raise ConfigChangeError(f"config apply failed and status moved to {status}: {exc}") from exc

    update_config_change_status(
        connection,
        change_id=change_id,
        status="applied",
        updated_at=utc_now(),
    )
    return _require_change(connection, change_id)


def _require_change(connection: sqlite3.Connection, change_id: str) -> dict[str, Any]:
    change = get_config_change(connection, change_id)
    if change is None:
        raise ConfigChangeError(f"config change not found: {change_id}")
    return change


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)


__all__ = [
    "ConfigChangeError",
    "ConfigChangeStatus",
    "VALID_CONFIG_CHANGE_STATUSES",
    "approve_config_change",
    "create_config_change",
    "reject_config_change",
]
