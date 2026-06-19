"""Worker command entrypoint."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.health import HealthThresholds
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.worker import SingleInstanceLockError, Worker
from stock_agent.worker.identity import build_worker_identity
from stock_agent.worker.pipeline import WorkerPipeline


def run_worker(
    root: Path,
    *,
    once: bool = False,
    interval_sec: float = 30,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> int:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    config = config_context.config
    connection = initialize_runtime_database(root, config)
    identity = build_worker_identity()
    lock_path = (root / config.storage.sqlite_path).with_suffix(".worker.lock")
    worker = Worker(
        connection,
        lock_path=lock_path,
        interval_sec=interval_sec,
        thresholds=HealthThresholds.from_config(config.health),
        identity=identity,
        pipeline=WorkerPipeline(
            root=root,
            config=config,
            connection=connection,
            notification_stream=output,
            now_fn=now_fn or _runtime_now_fn(),
            identity=identity,
        ),
    )

    exit_code = 1
    try:
        result = worker.run(once=once)
        output.write("worker_status=stopped\n" if result.stopped else "worker_status=completed\n")
        output.write(f"ticks={result.ticks}\n")
        output.write(f"errors={len(result.errors)}\n")
        if result.summaries:
            output.write("last_tick_summary:\n")
            for line in result.summaries[-1].lines():
                output.write(f"{line}\n")
        output.flush()
        exit_code = 0 if not result.errors else 1
    except SingleInstanceLockError as exc:
        output.write(f"worker_status=already_running\nerror={exc}\n")
        output.flush()
        exit_code = 1
    except KeyboardInterrupt:
        worker.request_stop()
        output.write("worker_status=stopped\nreason=keyboard_interrupt\n")
        output.flush()
        exit_code = 0
    finally:
        connection.close()
    return exit_code


def _runtime_now_fn() -> Callable[[], datetime]:
    fixed_now = os.getenv("STOCK_AGENT_NOW")
    if not fixed_now:
        from stock_agent.tracing import utc_now

        return utc_now
    parsed = datetime.fromisoformat(fixed_now.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    frozen = parsed.astimezone(UTC)
    return lambda: frozen


__all__ = ["run_worker"]
