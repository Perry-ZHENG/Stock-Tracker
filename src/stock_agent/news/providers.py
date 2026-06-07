"""News provider interfaces.

Real provider integrations can be added behind this interface later. T-107 keeps
the interface local-first so tests and demo mode do not require network access.
"""

from __future__ import annotations

from typing import Protocol

from stock_agent.schemas import NewsItem


class NewsProvider(Protocol):
    name: str

    def search(self, *, symbols: list[str], limit: int) -> list[NewsItem]:
        """Return news items for the requested symbols."""


class StaticNewsProvider:
    """Deterministic provider used by tests and local experiments."""

    name = "static"

    def __init__(self, items: list[NewsItem]) -> None:
        self.items = items

    def search(self, *, symbols: list[str], limit: int) -> list[NewsItem]:
        symbol_set = {symbol.upper() for symbol in symbols}
        matched = [
            item
            for item in self.items
            if not symbol_set or item.symbol is None or item.symbol.upper() in symbol_set
        ]
        return matched[:limit]


__all__ = ["NewsProvider", "StaticNewsProvider"]
