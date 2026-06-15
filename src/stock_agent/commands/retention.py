"""Retention review CLI command."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.storage.retention import RetentionPlan, build_retention_plan, execute_retention_plan, format_retention_plan


@dataclass(frozen=True)
class RetentionCommandResult:
    plan: RetentionPlan

    @property
    def ok(self) -> bool:
        return not self.plan.errors


def run_retention(
    root: Path,
    *,
    execute: bool = False,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> RetentionCommandResult:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    lake_root = root / config_context.config.storage.parquet_root
    plan = build_retention_plan(lake_root)
    result = execute_retention_plan(plan, execute=execute)
    output.write(format_retention_plan(result))
    output.flush()
    return RetentionCommandResult(plan=result)


__all__ = ["RetentionCommandResult", "run_retention"]
