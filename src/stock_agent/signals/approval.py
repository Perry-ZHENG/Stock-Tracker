"""Human-only approval boundary for signal activation and rollback."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.signals import SignalApproval, SignalVersion
from stock_agent.signals.registry import SignalRegistry, SignalRegistryError


class ApprovalRequest(StrictSchema):
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    decided_by: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2_000)


class SignalApprovalService:
    def __init__(self, *, registry: SignalRegistry, admin_ids: set[str]) -> None:
        self.registry = registry
        self.admin_ids = set(admin_ids)

    def approve(self, request: ApprovalRequest, *, now: datetime | None = None) -> tuple[SignalVersion, SignalApproval]:
        if request.actor_role != "admin" or request.decided_by not in self.admin_ids:
            raise SignalRegistryError("only an explicitly configured human admin can approve a signal version")
        active_now = _utc_now(now)
        version = self.registry.activate(
            signal_id=request.signal_id,
            version=request.version,
            approved_by=request.decided_by,
            now=active_now,
        )
        approval = SignalApproval(
            approval_id=f"approval-{uuid4().hex}",
            signal_id=request.signal_id,
            version=request.version,
            decision="approved",
            decided_by=request.decided_by,
            reason=request.reason,
            decided_at=active_now,
        )
        self.registry.repository.record_approval(approval)
        return version, approval


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("approval time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["ApprovalRequest", "SignalApprovalService"]
