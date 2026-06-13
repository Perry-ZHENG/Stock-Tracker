"""Read-only broker adapter boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from stock_agent.schemas import Bar, StrictSchema


class BrokerActionBlocked(PermissionError):
    """Raised for broker actions outside the read-only safety boundary."""


class BrokerCapabilities(StrictSchema):
    market_data: bool = False
    account_snapshot: bool = False
    positions_snapshot: bool = False
    broker_health: bool = False
    order_placement: bool = False
    order_modification: bool = False
    withdrawal: bool = False
    account_mutation: bool = False


class AccountSnapshot(StrictSchema):
    broker_name: str
    snapshot_at: datetime
    currency: str = "USD"
    cash_available: float = Field(ge=0)
    buying_power: float = Field(ge=0)
    equity: float = Field(ge=0)
    redacted_account_ref: str


class PositionSnapshot(StrictSchema):
    broker_name: str
    symbol: str
    quantity: float
    market_value: float
    average_cost: float | None = None
    snapshot_at: datetime


class BrokerHealth(StrictSchema):
    broker_name: str
    status: Literal["healthy", "degraded", "unhealthy"]
    checked_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class BrokerAdapter:
    """Base adapter exposes only read-only operations by default."""

    capabilities = BrokerCapabilities()

    def fetch_market_data(self, *args, **kwargs) -> list[Bar]:
        raise NotImplementedError("broker market data is not implemented")

    def get_account_snapshot(self) -> AccountSnapshot:
        raise NotImplementedError("broker account snapshot is not implemented")

    def get_positions_snapshot(self) -> list[PositionSnapshot]:
        raise NotImplementedError("broker positions snapshot is not implemented")

    def get_broker_health(self) -> BrokerHealth:
        raise NotImplementedError("broker health is not implemented")

    def place_order(self, *args, **kwargs):
        raise BrokerActionBlocked("order placement is blocked; stock-agent only provides observation signals")

    def modify_order(self, *args, **kwargs):
        raise BrokerActionBlocked("order modification is blocked; stock-agent only provides observation signals")

    def cancel_order(self, *args, **kwargs):
        raise BrokerActionBlocked("order cancellation is blocked; stock-agent only provides observation signals")

    def withdraw_funds(self, *args, **kwargs):
        raise BrokerActionBlocked("withdrawal is blocked; stock-agent never moves funds")

    def mutate_account(self, *args, **kwargs):
        raise BrokerActionBlocked("account mutation is blocked; stock-agent never changes account settings")


__all__ = [
    "AccountSnapshot",
    "BrokerActionBlocked",
    "BrokerAdapter",
    "BrokerCapabilities",
    "BrokerHealth",
    "PositionSnapshot",
]
