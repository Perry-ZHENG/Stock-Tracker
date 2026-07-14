"""Evidence, artifact, and deterministic research-input contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, TimeWindow, TrustLevel, ensure_utc

ArtifactKind = Literal[
    "bars",
    "news_body",
    "model_response",
    "candidate_source",
    "validation_metrics",
    "report",
    "analysis",
]
EvidenceType = Literal[
    "bar",
    "news",
    "signal",
    "trace",
    "provider",
    "mcp",
    "analysis",
]


class ArtifactRef(StrictSchema):
    artifact_id: str = Field(min_length=1)
    kind: ArtifactKind
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    created_at: datetime
    expires_at: datetime | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _validate_expiry(self) -> "ArtifactRef":
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class EvidenceRef(StrictSchema):
    evidence_id: str = Field(min_length=1)
    evidence_type: EvidenceType
    artifact_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    observed_at: datetime
    valid_until: datetime | None = None
    trust_level: TrustLevel = "medium"

    @field_validator("observed_at", "valid_until")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _validate_validity(self) -> "EvidenceRef":
        if self.valid_until is not None and self.valid_until < self.observed_at:
            raise ValueError("valid_until must not be earlier than observed_at")
        return self


class ProviderReference(StrictSchema):
    provider_name: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    observed_at: datetime
    fallback_used: bool = False

    @field_validator("observed_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class DataFeature(StrictSchema):
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(default="ratio", min_length=1)
    source_window: TimeWindow


class DataQuality(StrictSchema):
    status: Literal["normal", "degraded", "unavailable"] = "normal"
    missing_bar_count: int = Field(default=0, ge=0)
    quarantined_bar_count: int = Field(default=0, ge=0)
    flags: list[str] = Field(default_factory=list)


class DataEvidenceRequest(StrictSchema):
    symbols: list[str] = Field(min_length=1)
    time_window: TimeWindow
    interval: str = Field(default="30m", min_length=1)
    features: list[str] = Field(default_factory=list)
    baseline_window: int = Field(default=20, ge=1, le=10_000)
    freshness_seconds: int = Field(default=900, ge=0)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.upper() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("symbols must be non-empty and unique")
        return normalized


class DataEvidence(StrictSchema):
    request: DataEvidenceRequest
    bar_artifact: ArtifactRef
    summary: str = Field(min_length=1)
    features: list[DataFeature] = Field(default_factory=list)
    quality: DataQuality
    provider_refs: list[ProviderReference] = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_bar_artifact(self) -> "DataEvidence":
        if self.bar_artifact.kind != "bars":
            raise ValueError("DataEvidence.bar_artifact must have kind='bars'")
        return self


class NewsEvidenceRequest(StrictSchema):
    symbols: list[str] = Field(default_factory=list)
    time_window: TimeWindow
    topics: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.upper() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("symbols must be non-empty and unique")
        return normalized


class NewsCluster(StrictSchema):
    cluster_id: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    news_ids: list[str] = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class NewsCoverage(StrictSchema):
    requested_symbol_count: int = Field(ge=0)
    covered_symbol_count: int = Field(ge=0)
    source_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_coverage(self) -> "NewsCoverage":
        if self.covered_symbol_count > self.requested_symbol_count:
            raise ValueError("covered_symbol_count cannot exceed requested_symbol_count")
        return self


class NewsEvidence(StrictSchema):
    request: NewsEvidenceRequest
    clusters: list[NewsCluster] = Field(default_factory=list)
    source_count: int = Field(ge=0)
    coverage: NewsCoverage
    conflicts: list[str] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class EvidenceGapRequest(StrictSchema):
    task_id: str = Field(min_length=1)
    requester: Literal["signal_discovery", "anomaly_analysis", "macro_analysis", "report"]
    missing_evidence_types: list[EvidenceType] = Field(min_length=1)
    reason: str = Field(min_length=1)


class EvidenceBundle(StrictSchema):
    task_id: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_evidence_artifacts(self) -> "EvidenceBundle":
        artifact_ids = {artifact.artifact_id for artifact in self.artifact_refs}
        unknown = {reference.artifact_id for reference in self.evidence_refs} - artifact_ids
        if unknown:
            raise ValueError("EvidenceBundle contains evidence for unknown artifacts")
        return self


__all__ = [
    "ArtifactKind",
    "ArtifactRef",
    "DataEvidence",
    "DataEvidenceRequest",
    "DataFeature",
    "DataQuality",
    "EvidenceBundle",
    "EvidenceGapRequest",
    "EvidenceRef",
    "EvidenceType",
    "NewsCluster",
    "NewsCoverage",
    "NewsEvidence",
    "NewsEvidenceRequest",
    "ProviderReference",
]
