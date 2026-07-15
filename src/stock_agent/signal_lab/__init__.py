"""Restricted signal-function generation, execution, and validation components."""

from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG, FeatureCatalog
from stock_agent.signal_lab.interface import (
    CandidateBuildProvenance,
    CandidateBuildResult,
    CandidateFunctionDraft,
    SignalContext,
    SignalPoint,
)

__all__ = [
    "CandidateBuildProvenance",
    "CandidateBuildResult",
    "CandidateFunctionDraft",
    "DEFAULT_FEATURE_CATALOG",
    "FeatureCatalog",
    "SignalContext",
    "SignalPoint",
]
