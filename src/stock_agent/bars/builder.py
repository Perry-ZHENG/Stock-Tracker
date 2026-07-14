"""BarBuilder interface for current standard bars and future aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

from stock_agent.bars.aggregator import aggregate_to_interval
from stock_agent.bars.validation import BarValidationError, filter_regular_session, validate_bar, validate_bars
from stock_agent.schemas import Bar


@dataclass(frozen=True)
class EvidenceBarBuildResult:
    """Per-bar validation outcome used by the V2 evidence workflow.

    Legacy strategy entry points keep the fail-fast methods below. Evidence
    collection instead retains a bounded rejection record so downstream Agents
    can see that quality degraded without receiving invalid bars.
    """

    valid_bars: list[Bar]
    rejected: list[tuple[str, str]] = field(default_factory=list)


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

    def from_source_bars(
        self,
        bars: list[Bar],
        *,
        target_interval: str = "30m",
        source_interval: str = "1m",
    ) -> list[Bar]:
        aggregated = aggregate_to_interval(
            bars,
            target_interval=target_interval,
            source_interval=source_interval,
        ).bars
        return self.from_standard_bars(aggregated)

    def validate_for_evidence(self, bars: list[Bar]) -> EvidenceBarBuildResult:
        """Validate independently so one malformed provider bar is quarantined."""

        valid_bars: list[Bar] = []
        rejected: list[tuple[str, str]] = []
        for bar in bars:
            try:
                valid_bars.append(validate_bar(bar))
            except BarValidationError as exc:
                rejected.append((bar.bar_id, str(exc)))
        if self.regular_session_only:
            valid_bars = filter_regular_session(valid_bars)
        return EvidenceBarBuildResult(valid_bars=valid_bars, rejected=rejected)
