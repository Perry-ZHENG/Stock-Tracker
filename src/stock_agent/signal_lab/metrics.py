"""Reproducible validation metrics derived from Sandbox output artifacts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunMetrics:
    sample_count: int
    observation_count: int
    error_count: int
    deterministic: bool

    @property
    def coverage(self) -> float:
        return self.observation_count / self.sample_count if self.sample_count else 0.0

    @property
    def error_rate(self) -> float:
        return self.error_count / self.sample_count if self.sample_count else 1.0


def cross_symbol_consistency(symbol_observations: dict[str, int]) -> float | None:
    if len(symbol_observations) < 2:
        return None
    values = list(symbol_observations.values())
    maximum = max(values, default=0)
    if maximum == 0:
        return 1.0
    return min(values) / maximum


__all__ = ["RunMetrics", "cross_symbol_consistency"]
