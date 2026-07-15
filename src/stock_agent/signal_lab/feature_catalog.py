"""Versioned allowlist of feature arrays available to generated signal functions."""

from __future__ import annotations

from stock_agent.signal_lab.interface import FeatureCatalog, FeatureDefinition


DEFAULT_FEATURE_CATALOG = FeatureCatalog(
    version="market-v1",
    features=[
        FeatureDefinition(name="return_change", description="Bar-over-bar close return."),
        FeatureDefinition(name="volume_ratio", description="Current volume divided by historical baseline volume."),
        FeatureDefinition(name="realized_volatility", description="Historical realized volatility."),
        FeatureDefinition(name="gap", description="Open relative to the prior close."),
        FeatureDefinition(name="relative_to_baseline", description="Close relative to the selected price baseline."),
    ],
)


def proposal_feature_names(catalog: FeatureCatalog, names: list[str]) -> set[str]:
    """Normalize project feature names while rejecting anything outside the versioned allowlist."""

    normalized = {name.rsplit(".", 1)[-1] for name in names}
    unknown = normalized - catalog.names
    if unknown:
        raise ValueError(f"proposal requires features absent from FeatureCatalog: {sorted(unknown)}")
    return normalized


__all__ = ["DEFAULT_FEATURE_CATALOG", "FeatureCatalog", "proposal_feature_names"]
