"""BarBuilder interface for current standard bars and future aggregation."""

from __future__ import annotations

from dataclasses import dataclass, field

from stock_agent.bars.validation import BarValidationError, filter_regular_session, validate_bar, validate_bars
from stock_agent.schemas import Bar


@dataclass(frozen=True)
class EvidenceBarBuildResult:
    """Per-bar validation outcome used by the V2 evidence workflow.

    Evidence collection retains a bounded rejection record so downstream Agents
    can see quality degradation without receiving invalid bars.
    """

    valid_bars: list[Bar]
    rejected: list[tuple[str, str]] = field(default_factory=list)


class BarBuilder:
    """Prepare provider bars for deterministic V2 evidence validation."""

    def __init__(self, regular_session_only: bool = True) -> None:
        self.regular_session_only = regular_session_only

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
