"""Structured command intent schemas for CLI, Telegram, and LLM parsers."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from stock_agent.dialog.time_window import (
    normalize_explicit_time_window,
    requires_explicit_market_time,
)
from stock_agent.schemas import StrictSchema

IntentRisk = Literal["read_only", "pending_change", "local_admin", "high_risk_blocked"]
IntentSource = Literal["cli", "telegram", "llm", "structured_parser", "test"]
ReadOnlyQuery = Literal["signals", "health", "bars", "news", "stats", "trace", "schedule", "provider-compare", "abnormal-bars"]
PendingChangeAction = Literal[
    "add_symbol",
    "remove_symbol",
    "enable_strategy",
    "disable_strategy",
    "change_watch_window",
]
LocalAdminAction = Literal["init_config", "config_review", "approve_config", "reject_config", "reload_config"]
BlockedAction = Literal[
    "place_order",
    "modify_order",
    "cancel_order",
    "transfer_funds",
    "withdraw_funds",
    "read_secret",
    "change_password",
    "change_account",
    "unknown_high_risk",
]


class IntentBase(StrictSchema):
    source: IntentSource = "structured_parser"
    raw_text: str | None = None


class ReadOnlyIntent(IntentBase):
    intent_type: Literal["read_only"] = "read_only"
    risk: Literal["read_only"] = "read_only"
    executable: Literal[True] = True
    query: ReadOnlyQuery
    symbol: str | None = None
    symbols: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, gt=0, le=100)
    period: Literal["day", "month", "year"] | None = None
    target_id: str | None = None
    from_ts: str | None = None
    to_ts: str | None = None
    timezone: str | None = None

    @field_validator("symbol")
    @classmethod
    def _symbol_to_upper(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @field_validator("symbols")
    @classmethod
    def _symbols_to_upper(cls, value: list[str]) -> list[str]:
        return [symbol.upper() for symbol in value]

    @model_validator(mode="after")
    def _require_market_time_window(self) -> "ReadOnlyIntent":
        if requires_explicit_market_time(self.query, self.symbol):
            self.from_ts, self.to_ts = normalize_explicit_time_window(
                from_ts=self.from_ts,
                to_ts=self.to_ts,
                timezone_name=self.timezone,
            )
        return self


class PendingChangeIntent(IntentBase):
    intent_type: Literal["pending_change"] = "pending_change"
    risk: Literal["pending_change"] = "pending_change"
    executable: Literal[True] = True
    action: PendingChangeAction
    symbol: str | None = None
    strategy_id: str | None = None
    watch_window: dict[str, Any] | None = None
    reason: str | None = None

    @field_validator("symbol")
    @classmethod
    def _symbol_to_upper(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class LocalAdminIntent(IntentBase):
    intent_type: Literal["local_admin"] = "local_admin"
    risk: Literal["local_admin"] = "local_admin"
    executable: Literal[True] = True
    action: LocalAdminAction
    change_id: str | None = None
    dry_run: bool = True


class HighRiskBlockedIntent(IntentBase):
    intent_type: Literal["high_risk_blocked"] = "high_risk_blocked"
    risk: Literal["high_risk_blocked"] = "high_risk_blocked"
    executable: Literal[False] = False
    requested_action: BlockedAction
    blocked_reason: str
    safety_message: str = "本系统只提供观察信号，最终买卖由用户自行决定。"


class ClarificationIntent(IntentBase):
    intent_type: Literal["clarification"] = "clarification"
    risk: Literal["read_only"] = "read_only"
    executable: Literal[False] = False
    question: str
    candidates: list[str] = Field(default_factory=list)


CommandIntent = Annotated[
    ReadOnlyIntent | PendingChangeIntent | LocalAdminIntent | HighRiskBlockedIntent | ClarificationIntent,
    Field(discriminator="intent_type"),
]

_INTENT_ADAPTER = TypeAdapter(CommandIntent)


def validate_intent(payload: dict[str, Any]) -> CommandIntent:
    """Validate a parser or LLM result before any command handler can run."""

    return _INTENT_ADAPTER.validate_python(payload)


def intent_json_schema() -> dict[str, Any]:
    return _INTENT_ADAPTER.json_schema()


__all__ = [
    "BlockedAction",
    "ClarificationIntent",
    "CommandIntent",
    "HighRiskBlockedIntent",
    "IntentRisk",
    "IntentSource",
    "LocalAdminAction",
    "LocalAdminIntent",
    "PendingChangeAction",
    "PendingChangeIntent",
    "ReadOnlyIntent",
    "ReadOnlyQuery",
    "intent_json_schema",
    "validate_intent",
]
