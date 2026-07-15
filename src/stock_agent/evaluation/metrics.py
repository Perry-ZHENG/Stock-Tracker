"""Pure metrics used by deterministic V2 benchmark fixtures."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Mapping


def rate(values: Iterable[bool]) -> float | None:
    """Return a rate, preserving a no-sample state instead of inventing success."""

    normalized = list(values)
    if not normalized:
        return None
    return sum(normalized) / len(normalized)


def evidence_precision(claim_evidence_ids: Iterable[str], available_evidence_ids: Iterable[str]) -> float | None:
    claimed = list(claim_evidence_ids)
    available = set(available_evidence_ids)
    if not claimed:
        return None
    return sum(reference in available for reference in claimed) / len(claimed)


def evidence_coverage(claim_evidence_ids: Iterable[str], required_evidence_ids: Iterable[str]) -> float | None:
    required = set(required_evidence_ids)
    if not required:
        return None
    claimed = set(claim_evidence_ids)
    return sum(reference in claimed for reference in required) / len(required)


def numeric_consistency(values: Iterable[tuple[float, float]]) -> float | None:
    pairs = list(values)
    if not pairs:
        return None
    return sum(expected == reported for expected, reported in pairs) / len(pairs)


def unsupported_claim_rate(claim_evidence_ids: Iterable[str], available_evidence_ids: Iterable[str]) -> float:
    claimed = list(claim_evidence_ids)
    if not claimed:
        return 0.0
    available = set(available_evidence_ids)
    return sum(reference not in available for reference in claimed) / len(claimed)


def compare_baseline(
    current: Mapping[str, float | None],
    baseline: Mapping[str, float],
    *,
    allowed_regression: float = 0.0,
) -> list[str]:
    """Return deterministic failure labels for missing or regressed metric values."""

    failures: list[str] = []
    for name, baseline_value in sorted(baseline.items()):
        current_value = current.get(name)
        if current_value is None:
            failures.append(f"baseline_missing:{name}")
        elif current_value + allowed_regression < baseline_value:
            failures.append(f"baseline_regression:{name}")
    return failures


__all__ = [
    "compare_baseline",
    "evidence_coverage",
    "evidence_precision",
    "numeric_consistency",
    "rate",
    "unsupported_claim_rate",
]
