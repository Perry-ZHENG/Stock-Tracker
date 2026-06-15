"""Crash recovery and restart-budget helpers for worker runs."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from stock_agent.storage.repositories import get_checkpoint, insert_notification, upsert_checkpoint
from stock_agent.tracing import utc_now


@dataclass(frozen=True)
class CrashBudgetState:
    crash_count: int
    restart_attempts: int
    last_failure: str | None = None
    stopped: bool = False


class CrashBudgetExceeded(RuntimeError):
    """Raised when worker crash or recovery budgets are exhausted."""


class CrashRecoveryManager:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        crash_limit: int = 10,
        recovery_limit: int = 5,
        checkpoint_id: str = "worker:crash_budget",
    ) -> None:
        self.connection = connection
        self.crash_limit = crash_limit
        self.recovery_limit = recovery_limit
        self.checkpoint_id = checkpoint_id

    def state(self) -> CrashBudgetState:
        row = get_checkpoint(self.connection, self.checkpoint_id)
        if row is None:
            return CrashBudgetState(crash_count=0, restart_attempts=0)
        parts = _parse_checkpoint(str(row["checkpoint_value"]))
        return CrashBudgetState(
            crash_count=int(parts.get("crash_count", "0")),
            restart_attempts=int(parts.get("restart_attempts", "0")),
            last_failure=parts.get("last_failure") or None,
            stopped=parts.get("stopped") == "true",
        )

    def record_crash(self, error: str) -> CrashBudgetState:
        previous = self.state()
        next_state = CrashBudgetState(
            crash_count=previous.crash_count + 1,
            restart_attempts=previous.restart_attempts,
            last_failure=error,
            stopped=previous.crash_count + 1 >= self.crash_limit,
        )
        self._persist(next_state)
        if next_state.stopped:
            self.notify_failure("crash budget exceeded", next_state)
            raise CrashBudgetExceeded("worker crash budget exceeded")
        return next_state

    def record_recovery_attempt(self, error: str | None = None) -> CrashBudgetState:
        previous = self.state()
        next_state = CrashBudgetState(
            crash_count=previous.crash_count,
            restart_attempts=previous.restart_attempts + 1,
            last_failure=error or previous.last_failure,
            stopped=previous.restart_attempts + 1 >= self.recovery_limit,
        )
        self._persist(next_state)
        if next_state.stopped:
            self.notify_failure("recovery budget exceeded", next_state)
            raise CrashBudgetExceeded("worker recovery budget exceeded")
        return next_state

    def reset_after_success(self) -> None:
        self._persist(CrashBudgetState(crash_count=0, restart_attempts=0))

    def notify_failure(self, message: str, state: CrashBudgetState) -> None:
        now = utc_now()
        insert_notification(
            self.connection,
            notification_id=f"notif-worker-recovery-{message.replace(' ', '-')}",
            channel="worker",
            status="pending",
            payload={
                "type": "worker_failure",
                "message": message,
                "crash_count": state.crash_count,
                "restart_attempts": state.restart_attempts,
                "last_failure": state.last_failure,
            },
            retry_count=0,
            error_msg=None,
            created_at=now,
            updated_at=now,
        )

    def _persist(self, state: CrashBudgetState) -> None:
        upsert_checkpoint(
            self.connection,
            checkpoint_id=self.checkpoint_id,
            module="worker",
            checkpoint_key="crash_budget",
            checkpoint_value=_format_checkpoint(state),
            updated_at=utc_now(),
        )


def _format_checkpoint(state: CrashBudgetState) -> str:
    return ";".join(
        [
            f"crash_count={state.crash_count}",
            f"restart_attempts={state.restart_attempts}",
            f"last_failure={state.last_failure or ''}",
            f"stopped={str(state.stopped).lower()}",
        ]
    )


def _parse_checkpoint(value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in value.split(";"):
        key, separator, raw_value = item.partition("=")
        if separator:
            parts[key] = raw_value
    return parts


__all__ = ["CrashBudgetExceeded", "CrashBudgetState", "CrashRecoveryManager"]
