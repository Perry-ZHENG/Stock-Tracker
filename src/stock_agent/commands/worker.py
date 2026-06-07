"""Worker command entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.health import HealthThresholds
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.worker import SingleInstanceLockError, Worker


def run_worker(
    root: Path,
    *,
    once: bool = False,
    interval_sec: float = 30,
    stream: TextIO | None = None,
) -> int:
    output = stream or sys.stdout
    config = validate_config(DEFAULT_CONFIG)
    connection = initialize_runtime_database(root, config)
    lock_path = root / "data" / "runtime" / "stock_agent.worker.lock"
    worker = Worker(
        connection,
        lock_path=lock_path,
        interval_sec=interval_sec,
        thresholds=HealthThresholds.from_config(config.health),
    )

    try:
        result = worker.run(once=once)
    except SingleInstanceLockError as exc:
        output.write(f"worker_status=already_running\nerror={exc}\n")
        output.flush()
        return 1
    except KeyboardInterrupt:
        worker.request_stop()
        output.write("worker_status=stopped\nreason=keyboard_interrupt\n")
        output.flush()
        return 0

    output.write("worker_status=stopped\n" if result.stopped else "worker_status=completed\n")
    output.write(f"ticks={result.ticks}\n")
    output.write(f"errors={len(result.errors)}\n")
    output.flush()
    return 0 if not result.errors else 1


__all__ = ["run_worker"]
