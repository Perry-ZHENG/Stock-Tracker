"""CLI handlers for configuration review."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from stock_agent.config_changes import ConfigChangeError, approve_config_change, reject_config_change
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.storage.repositories import get_config_change, list_config_changes
from stock_agent.storage.sqlite import open_database


def run_config_review(
    root: Path,
    *,
    action: str,
    change_id: str | None = None,
    limit: int = 10,
    config_path: Path | None = None,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> int:
    output = stream or sys.stdout
    config_context = config_context or load_config(root, config_path=config_path)
    config = config_context.config
    target_config_path = config_path or config_context.config_path
    sqlite_path = root / config.storage.sqlite_path
    if not sqlite_path.exists():
        output.write(f"review_error=no runtime database\nsqlite_path={sqlite_path}\n")
        output.flush()
        return 1

    connection = open_database(sqlite_path)
    try:
        if action == "review":
            output.write(_format_review(connection, change_id=change_id, limit=limit))
        elif action == "approve":
            if change_id is None:
                raise ConfigChangeError("approve requires change_id")
            change = approve_config_change(
                connection,
                change_id=change_id,
                config_path=target_config_path,
                reload_validator=lambda _raw_config: load_config(root, config_path=target_config_path),
            )
            output.write(f"config_change={change['change_id']} status={change['status']}\n")
        elif action == "reject":
            if change_id is None:
                raise ConfigChangeError("reject requires change_id")
            change = reject_config_change(connection, change_id=change_id)
            output.write(f"config_change={change['change_id']} status={change['status']}\n")
        else:
            raise ConfigChangeError(f"unsupported config review action: {action}")
    except ConfigChangeError as exc:
        output.write(f"review_error={exc}\n")
        output.flush()
        return 1

    output.flush()
    return 0


def _format_review(connection, *, change_id: str | None, limit: int) -> str:
    if change_id:
        change = get_config_change(connection, change_id)
        rows = [change] if change else []
    else:
        rows = list_config_changes(connection, limit=limit)
    if not rows:
        return "config_changes: no rows\n"

    lines = ["change_id | status | source | diff"]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row["change_id"]),
                    str(row["status"]),
                    str(row["source"]),
                    str(row.get("diff") or ""),
                ]
            )
        )
    return "\n".join(lines) + "\n"


__all__ = ["run_config_review"]
