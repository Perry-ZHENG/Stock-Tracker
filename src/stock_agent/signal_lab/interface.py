"""Pure interfaces shared by generated candidates, Sandbox, and Runner."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.contracts.evidence import ArtifactRef
from stock_agent.contracts.signals import CandidateFunction, SignalLabel, SignalProposal


class FeatureDefinition(StrictSchema):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    unit: str = Field(default="ratio", min_length=1, max_length=64)


class FeatureCatalog(StrictSchema):
    version: str = Field(min_length=1, max_length=128)
    features: list[FeatureDefinition] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique_feature_names(self) -> "FeatureCatalog":
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("FeatureCatalog feature names must be unique")
        return self

    @property
    def names(self) -> set[str]:
        return {feature.name for feature in self.features}


class SignalContext(StrictSchema):
    """Serializable, feature-only candidate input; never contains a database or tool handle."""

    catalog_version: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timestamps: tuple[datetime, ...] = Field(min_length=1, max_length=100_000)
    features: dict[str, tuple[float, ...]] = Field(min_length=1)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return value.upper()

    @field_validator("timestamps")
    @classmethod
    def _normalize_timestamps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(ensure_utc(value) for value in values)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _validate_feature_lengths(self) -> "SignalContext":
        if any(len(values) != len(self.timestamps) for values in self.features.values()):
            raise ValueError("every SignalContext feature array must match timestamps")
        return self

    def validate_catalog(self, catalog: FeatureCatalog) -> None:
        if self.catalog_version != catalog.version:
            raise ValueError("SignalContext catalog version does not match FeatureCatalog")
        unknown = set(self.features) - catalog.names
        if unknown:
            raise ValueError(f"SignalContext contains unknown features: {sorted(unknown)}")


class SignalPoint(StrictSchema):
    timestamp: datetime
    label: SignalLabel
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1_000)

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class CandidateFunctionDraft(StrictSchema):
    """The only LLM output accepted by CandidateBuilder before AST policy review."""

    interface_version: Literal["signal_context_v1"] = "signal_context_v1"
    required_features: list[str] = Field(min_length=1, max_length=32)
    source_code: str = Field(min_length=1, max_length=24_000)

    @field_validator("required_features")
    @classmethod
    def _unique_features(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("CandidateFunctionDraft required_features must be unique")
        return values


class CandidateBuildProvenance(StrictSchema):
    candidate_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    proposal: SignalProposal
    prompt_artifact: ArtifactRef
    model_id: str = Field(min_length=1, max_length=256)
    feature_catalog: FeatureCatalog
    history_artifact: ArtifactRef
    build_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_candidate_id: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class CandidateBuildResult(StrictSchema):
    candidate: CandidateFunction
    provenance: CandidateBuildProvenance
    prior_candidate_ids: list[str] = Field(default_factory=list)


__all__ = [
    "CandidateBuildProvenance",
    "CandidateBuildResult",
    "CandidateFunctionDraft",
    "FeatureCatalog",
    "FeatureDefinition",
    "SignalContext",
    "SignalPoint",
]
