"""Trading-action firewall for CLI, Telegram, LLM, and worker boundaries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from stock_agent.dialog.intents import BlockedAction, CommandIntent, HighRiskBlockedIntent
from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.storage.repositories import insert_security_audit
from stock_agent.tracing import utc_now

OBSERVATION_ONLY_MESSAGE = "本系统只提供观察信号，最终买卖由用户自行决定。"
SECRET_ACCESS_BLOCKED_MESSAGE = "credential requests are blocked; API keys, tokens, and environment secrets are never displayed by this CLI."
BLOCKED_DECISION = "blocked"

_TRADING_AND_MUTATION_ACTIONS: set[BlockedAction] = {
    "place_order",
    "modify_order",
    "cancel_order",
    "transfer_funds",
    "withdraw_funds",
    "read_secret",
    "change_password",
    "change_account",
    "unknown_high_risk",
}


@dataclass(frozen=True)
class FirewallDecision:
    allowed: bool
    action: str | None = None
    message: str = ""
    audit_id: str | None = None


class TradingActionFirewall:
    """Reject high-risk actions before they reach services or broker adapters."""

    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection

    def inspect_intent(
        self,
        intent: CommandIntent,
        *,
        source: str | None = None,
        actor_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FirewallDecision:
        if not isinstance(intent, HighRiskBlockedIntent):
            return FirewallDecision(allowed=True)
        return self.block(
            action=intent.requested_action,
            source=source or intent.source,
            actor_ref=actor_ref,
            raw_text=intent.raw_text,
            reason=intent.blocked_reason,
            details={
                "intent_type": intent.intent_type,
                "risk": intent.risk,
                **(details or {}),
            },
        )

    def block(
        self,
        *,
        action: str,
        source: str,
        actor_ref: str | None = None,
        raw_text: str | None = None,
        reason: str = "trading firewall blocks trading, credential, account, and money movement actions",
        details: dict[str, Any] | None = None,
    ) -> FirewallDecision:
        audit_id = None
        if self.connection is not None:
            audit_id = insert_security_audit(
                self.connection,
                timestamp=utc_now(),
                source=source,
                actor_ref=actor_ref,
                action=action,
                decision=BLOCKED_DECISION,
                reason=reason,
                raw_text=raw_text,
                details=redact_sensitive(details or {}),
            )
        return FirewallDecision(
            allowed=False,
            action=action,
            message=OBSERVATION_ONLY_MESSAGE,
            audit_id=audit_id,
        )


def is_firewall_blocked_action(action: str) -> bool:
    return action in _TRADING_AND_MUTATION_ACTIONS


def blocked_message(action: str, *, audit_id: str | None = None) -> str:
    redacted_action = redact_text(action) or "unknown_high_risk"
    suffix = f"\naudit_id={audit_id}" if audit_id else ""
    if redacted_action == "read_secret":
        return f"blocked={redacted_action}\n{SECRET_ACCESS_BLOCKED_MESSAGE}{suffix}\n"
    return f"blocked={redacted_action}\n{OBSERVATION_ONLY_MESSAGE}{suffix}\n"


__all__ = [
    "BLOCKED_DECISION",
    "FirewallDecision",
    "OBSERVATION_ONLY_MESSAGE",
    "SECRET_ACCESS_BLOCKED_MESSAGE",
    "TradingActionFirewall",
    "blocked_message",
    "is_firewall_blocked_action",
]
