"""Shared strict schemas and primitives for the V2 research domain."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AgentRole = Literal[
    "orchestrator",
    "signal_discovery",
    "anomaly_analysis",
    "macro_analysis",
    "report",
]
TaskStatus = Literal["pending", "running", "paused", "cancelled", "failed", "completed"]
StepStatus = Literal["pending", "running", "succeeded", "failed", "skipped", "cancelled"]
TrustLevel = Literal["low", "medium", "high"]


def ensure_utc(value: datetime | None) -> datetime | None:
    """Reject naive datetimes and normalize timezone-aware values to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime fields must be timezone-aware")
    return value.astimezone(UTC)


class StrictSchema(BaseModel):
    """Base model for every V2 boundary; unknown fields are always rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TimeWindow(StrictSchema):
    """A timezone-aware, closed research window."""

    from_ts: datetime
    to_ts: datetime
    timezone: str = Field(min_length=1)

    @field_validator("from_ts", "to_ts")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def _validate_order(self) -> "TimeWindow":
        if self.to_ts <= self.from_ts:
            raise ValueError("to_ts must be later than from_ts")
        return self


class ExecutionBudget(StrictSchema):
    """Hard limits carried with an AgentTask and enforced by the runtime."""

    max_agent_steps: int = Field(default=12, ge=1, le=100)
    max_tool_calls: int = Field(default=24, ge=0, le=500)
    max_model_calls: int = Field(default=8, ge=0, le=100)
    max_duration_seconds: int = Field(default=900, ge=1, le=86_400)


__all__ = [
    "AgentRole",
    "ExecutionBudget",
    "StepStatus",
    "StrictSchema",
    "TaskStatus",
    "TimeWindow",
    "TrustLevel",
    "ensure_utc",
]
