"""Background worker skeleton with heartbeat and single-instance locking."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.tracing import utc_now
from stock_agent.worker.identity import WorkerIdentity, build_worker_identity
from stock_agent.worker.pipeline import WorkerPipeline, WorkerTickSummary
from stock_agent.worker.recovery import CrashBudgetExceeded, CrashRecoveryManager


class SingleInstanceLockError(RuntimeError):
    """Raised when another worker instance already owns the lock."""


class SingleInstanceLock:
    def __init__(self, path: Path, *, identity: WorkerIdentity | None = None) -> None:
        self.path = path
        self.identity = identity or build_worker_identity()
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(
                self._fd,
                (
                    f"pid={os.getpid()}\n"
                    f"host_id={self.identity.host_id}\n"
                    f"instance_id={self.identity.instance_id}\n"
                    f"lock_owner={self.identity.lock_owner()}\n"
                    f"multi_instance_enabled={str(self.identity.multi_instance_enabled).lower()}\n"
                ).encode("utf-8"),
            )
        except FileExistsError as exc:
            raise SingleInstanceLockError(f"worker lock already exists: {self.path}") from exc

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


@dataclass(frozen=True)
class WorkerRunResult:
    ticks: int
    stopped: bool
    errors: list[str] = field(default_factory=list)
    summaries: list[WorkerTickSummary] = field(default_factory=list)


class Worker:
    """Minimal worker loop.

    Future tasks can attach market-data polling, strategy execution, notification,
    crash recovery, and gap filling behind the placeholder hooks below.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        lock_path: Path,
        interval_sec: float = 30,
        thresholds: HealthThresholds | None = None,
        pipeline: WorkerPipeline | None = None,
        recovery_manager: CrashRecoveryManager | None = None,
        identity: WorkerIdentity | None = None,
    ) -> None:
        self.connection = connection
        self.lock_path = lock_path
        self.interval_sec = interval_sec
        self.thresholds = thresholds or HealthThresholds()
        self.pipeline = pipeline
        self.recovery_manager = recovery_manager or CrashRecoveryManager(connection)
        self.identity = identity or build_worker_identity()
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(
        self,
        *,
        once: bool = False,
        max_ticks: int | None = None,
    ) -> WorkerRunResult:
        ticks = 0
        errors: list[str] = []
        summaries: list[WorkerTickSummary] = []
        with SingleInstanceLock(self.lock_path, identity=self.identity):
            try:
                self.recover_from_crash()
            except CrashBudgetExceeded as exc:
                errors.append(str(exc))
                return WorkerRunResult(ticks=ticks, stopped=True, errors=errors, summaries=summaries)
            self.fill_data_gaps()
            while not self._stop_requested:
                try:
                    summaries.append(self.tick())
                    self.recovery_manager.reset_after_success()
                    ticks += 1
                except Exception as exc:  # pragma: no cover - safety boundary for future worker jobs
                    errors.append(str(exc))
                    try:
                        self.recovery_manager.record_crash(str(exc))
                    except CrashBudgetExceeded as budget_exc:
                        errors.append(str(budget_exc))
                        self._stop_requested = True
                    record_health_metric(
                        self.connection,
                        module="worker",
                        data_latency_sec=0,
                        error_rate=1,
                        consecutive_failures=len(errors),
                        core_module_running=True,
                details={"error": str(exc)},
                        thresholds=self.thresholds,
                    )
                if once or (max_ticks is not None and ticks >= max_ticks):
                    break
                time.sleep(self.interval_sec)
        return WorkerRunResult(ticks=ticks, stopped=self._stop_requested, errors=errors, summaries=summaries)

    def tick(self) -> WorkerTickSummary:
        if self.pipeline is not None:
            return self.pipeline.run_once()

        now = utc_now()
        record_health_metric(
            self.connection,
            module="worker",
            heartbeat_at=now,
            data_latency_sec=0,
            error_rate=0,
            consecutive_failures=0,
            alert_failures=0,
            core_module_running=True,
            details={
                "loop": "skeleton",
                "crash_recovery": "placeholder",
                "gap_fill": "placeholder",
                "instance_id": self.identity.instance_id,
                "host_id": self.identity.host_id,
                "lock_owner": self.identity.lock_owner(),
                "multi_instance_enabled": self.identity.multi_instance_enabled,
            },
            now=now,
            thresholds=self.thresholds,
        )
        return WorkerTickSummary(status="heartbeat", trading_day=True)

    def recover_from_crash(self) -> list[str]:
        state = self.recovery_manager.state()
        if state.crash_count > 0 or state.last_failure:
            self.recovery_manager.record_recovery_attempt(state.last_failure)
            return [state.last_failure or "previous worker crash"]
        return []

    def fill_data_gaps(self) -> list[str]:
        return []
