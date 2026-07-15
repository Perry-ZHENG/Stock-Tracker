"""Restart-safe, atomic resource accounting for one V2 research task."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from threading import RLock

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.contracts.tasks import AgentTask
from stock_agent.storage.task_repository import TaskRepository


class BudgetExceeded(RuntimeError):
    """A task attempted to consume a hard model or Tool call limit."""


class BudgetSnapshot(StrictSchema):
    """Durable consumption totals and remaining hard-call capacity."""

    task_id: str = Field(min_length=1)
    max_model_calls: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    used_model_calls: int = Field(default=0, ge=0)
    used_tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    sandbox_cpu_ms: int = Field(default=0, ge=0)
    sandbox_memory_mb_ms: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _normalize_updated_at(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_limits(self) -> "BudgetSnapshot":
        if self.used_model_calls > self.max_model_calls:
            raise ValueError("used_model_calls cannot exceed max_model_calls")
        if self.used_tool_calls > self.max_tool_calls:
            raise ValueError("used_tool_calls cannot exceed max_tool_calls")
        return self

    @property
    def remaining_model_calls(self) -> int:
        return self.max_model_calls - self.used_model_calls

    @property
    def remaining_tool_calls(self) -> int:
        return self.max_tool_calls - self.used_tool_calls


class BudgetLedger:
    """Use SQLite transactions so concurrent workers cannot overspend a task budget."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.repository = TaskRepository(connection)
        self._lock = RLock()

    def ensure(self, task: AgentTask, *, now: datetime | None = None) -> BudgetSnapshot:
        active_now = _utc_now(now)
        with self._lock:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO agent_budget_snapshots (
                    task_id, max_model_calls, max_tool_calls, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.budget.max_model_calls,
                    task.budget.max_tool_calls,
                    _timestamp(active_now),
                ),
            )
            self.connection.commit()
            snapshot = self.get(task.task_id)
            assert snapshot is not None
            return snapshot

    def get(self, task_id: str) -> BudgetSnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM agent_budget_snapshots WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _snapshot(row) if row is not None else None

    def consume(
        self,
        task_id: str,
        *,
        model_calls: int = 0,
        tool_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd: float = 0,
        sandbox_cpu_ms: int = 0,
        sandbox_memory_mb_ms: int = 0,
        now: datetime | None = None,
    ) -> BudgetSnapshot:
        values = (model_calls, tool_calls, input_tokens, output_tokens, sandbox_cpu_ms, sandbox_memory_mb_ms)
        if any(value < 0 for value in values) or estimated_cost_usd < 0:
            raise ValueError("budget increments must be non-negative")
        active_now = _utc_now(now)
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT * FROM agent_budget_snapshots WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    task = self.repository.get_task(task_id)
                    if task is None:
                        raise ValueError(f"task does not exist: {task_id}")
                    self.connection.execute(
                        """
                        INSERT INTO agent_budget_snapshots (
                            task_id, max_model_calls, max_tool_calls, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (task.task_id, task.budget.max_model_calls, task.budget.max_tool_calls, _timestamp(active_now)),
                    )
                    row = self.connection.execute(
                        "SELECT * FROM agent_budget_snapshots WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                assert row is not None
                snapshot = _snapshot(row)
                if snapshot.used_model_calls + model_calls > snapshot.max_model_calls:
                    raise BudgetExceeded("model-call budget is exhausted")
                if snapshot.used_tool_calls + tool_calls > snapshot.max_tool_calls:
                    raise BudgetExceeded("tool-call budget is exhausted")
                self.connection.execute(
                    """
                    UPDATE agent_budget_snapshots
                    SET used_model_calls = used_model_calls + ?,
                        used_tool_calls = used_tool_calls + ?,
                        input_tokens = input_tokens + ?,
                        output_tokens = output_tokens + ?,
                        estimated_cost_usd = estimated_cost_usd + ?,
                        sandbox_cpu_ms = sandbox_cpu_ms + ?,
                        sandbox_memory_mb_ms = sandbox_memory_mb_ms + ?,
                        revision = revision + 1,
                        updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        model_calls,
                        tool_calls,
                        input_tokens,
                        output_tokens,
                        estimated_cost_usd,
                        sandbox_cpu_ms,
                        sandbox_memory_mb_ms,
                        _timestamp(active_now),
                        task_id,
                    ),
                )
                updated = self.connection.execute(
                    "SELECT * FROM agent_budget_snapshots WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                self.connection.commit()
                assert updated is not None
                return _snapshot(updated)
            except Exception:
                self.connection.rollback()
                raise


def _snapshot(row) -> BudgetSnapshot:
    return BudgetSnapshot(
        task_id=row["task_id"],
        max_model_calls=row["max_model_calls"],
        max_tool_calls=row["max_tool_calls"],
        used_model_calls=row["used_model_calls"],
        used_tool_calls=row["used_tool_calls"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        estimated_cost_usd=row["estimated_cost_usd"],
        sandbox_cpu_ms=row["sandbox_cpu_ms"],
        sandbox_memory_mb_ms=row["sandbox_memory_mb_ms"],
        revision=row["revision"],
        updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
    )


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("budget time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["BudgetExceeded", "BudgetLedger", "BudgetSnapshot"]
