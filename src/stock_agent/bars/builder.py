"""BarBuilder interface for current standard bars and future aggregation."""

from __future__ import annotations

from stock_agent.bars.validation import filter_regular_session, validate_bars
from stock_agent.schemas import Bar


class BarBuilder:
    """Prepare bars for v1 strategy calculation.

    T-008 only supports already-standard bars, such as CSV demo 30m bars.
    Future tasks can add tick/1m aggregation behind this interface.
    """

    def __init__(self, regular_session_only: bool = True) -> None:
        self.regular_session_only = regular_session_only

    def from_standard_bars(self, bars: list[Bar]) -> list[Bar]:
        validated_bars = validate_bars(bars)
        if self.regular_session_only:
            return filter_regular_session(validated_bars)
        return validated_bars
