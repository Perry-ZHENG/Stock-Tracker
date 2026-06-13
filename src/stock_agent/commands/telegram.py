"""Telegram command entrypoint skeleton."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config


def run_telegram(
    root: Path,
    *,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> int:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    config = config_context.config
    token = os.getenv(config.telegram.token_env)

    if not config.telegram.enabled:
        output.write("telegram_status=disabled\nreason=telegram.enabled is false\n")
        output.flush()
        return 0
    if not token:
        output.write(
            f"telegram_status=disabled\nreason=missing token env {config.telegram.token_env}\n"
        )
        output.flush()
        return 0

    output.write("telegram_status=ready\n")
    output.write("listener=skeleton\n")
    output.write(f"workspace={root}\n")
    output.flush()
    return 0


__all__ = ["run_telegram"]
