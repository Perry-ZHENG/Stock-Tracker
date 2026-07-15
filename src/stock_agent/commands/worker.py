"""Worker command entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.health import HealthThresholds
from stock_agent.services.production_v2 import build_production_v2
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.worker import ResearchTaskWorkerV2, ResearchWorkerPipelineV2, SingleInstanceLockError, Worker
from stock_agent.worker.identity import build_worker_identity


def run_worker(
    root: Path,
    *,
    once: bool = False,
    interval_sec: float = 30,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> int:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    config = config_context.config
    connection = initialize_runtime_database(root, config)
    identity = build_worker_identity()
    lock_path = (root / config.storage.sqlite_path).with_suffix(".worker.lock")
    v2_components = build_production_v2(root, config_context=config_context, connection=connection)
    worker = Worker(
        connection,
        lock_path=lock_path,
        interval_sec=interval_sec,
        thresholds=HealthThresholds.from_config(config.health),
        identity=identity,
        pipeline=ResearchWorkerPipelineV2(
            research_worker=ResearchTaskWorkerV2(v2_components.service, worker_id=identity.instance_id),
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
        v2_components.close()
    return exit_code


__all__ = ["run_worker"]
