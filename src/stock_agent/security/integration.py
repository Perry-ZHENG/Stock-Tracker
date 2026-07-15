"""Release-gate safety coverage summary for V2's bounded research capabilities."""

from __future__ import annotations

import sqlite3

from pydantic import Field

from stock_agent.contracts.common import StrictSchema
from stock_agent.storage.repositories import list_security_audit


class CapabilityMatrixEntry(StrictSchema):
    boundary: str = Field(min_length=1)
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(min_length=1)


class SafetyIntegrationReport(StrictSchema):
    policy_version: str = "research-safety-v2"
    boundaries: list[CapabilityMatrixEntry] = Field(min_length=1)
    audited_block_count: int = Field(ge=0)


def build_safety_integration_report(connection: sqlite3.Connection) -> SafetyIntegrationReport:
    """Expose a static capability matrix plus the persisted blocked-decision count."""

    return SafetyIntegrationReport(
        boundaries=[
            CapabilityMatrixEntry(
                boundary="interface",
                allowed_capabilities=["research"],
                denied_capabilities=["place_order", "approve_signal", "run_signal_sandbox"],
            ),
            CapabilityMatrixEntry(
                boundary="agent_service_orchestrator",
                allowed_capabilities=["research"],
                denied_capabilities=["place_order", "bypass_approval", "use_unapproved_tool"],
            ),
            CapabilityMatrixEntry(
                boundary="tool_gateway_mcp",
                allowed_capabilities=["read_market_data", "read_news", "use_mcp"],
                denied_capabilities=["place_order", "read_secret", "host_execution"],
            ),
            CapabilityMatrixEntry(
                boundary="signal_sandbox_registry",
                allowed_capabilities=["run_signal_sandbox", "write_signal_candidate"],
                denied_capabilities=["approve_signal_for_agent", "host_execution", "place_order"],
            ),
            CapabilityMatrixEntry(
                boundary="report_validator",
                allowed_capabilities=["write_report"],
                denied_capabilities=["place_order", "guaranteed_return"],
            ),
        ],
        audited_block_count=len(list_security_audit(connection)),
    )


__all__ = ["CapabilityMatrixEntry", "SafetyIntegrationReport", "build_safety_integration_report"]
