"""Signal-discovery, candidate, validation, and observation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, TimeWindow, ensure_utc
from stock_agent.contracts.evidence import (
    ArtifactRef,
    DataEvidence,
    EvidenceRef,
    NewsEvidence,
)
SignalLabel = Literal["positive", "negative", "neutral", "uncertain"]
SignalVersionStatus = Literal["draft", "validating", "validated", "active", "suspended", "retired"]
ValidationDecision = Literal["pass", "revise", "reject"]


class SignalFeature(StrictSchema):
    name: str = Field(min_length=1)
    source: Literal["market", "news", "event"]
    description: str = Field(min_length=1)


class ExistingSignal(StrictSchema):
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    feature_fingerprint: str = Field(min_length=1)
    status: SignalVersionStatus


class SignalValidationFeedback(StrictSchema):
    candidate_id: str = Field(min_length=1)
    decision: ValidationDecision
    reasons: list[str] = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SignalDiscoveryConstraints(StrictSchema):
    max_revisions: int = Field(default=2, ge=0, le=10)
    allow_news_features: bool = False


class SignalDiscoveryInput(StrictSchema):
    goal: str = Field(min_length=1)
    data_evidence: list[DataEvidence] = Field(min_length=1)
    history_artifacts: list[ArtifactRef] = Field(min_length=1)
    news_evidence: list[NewsEvidence] = Field(default_factory=list)
    existing_signals: list[ExistingSignal] = Field(default_factory=list)
    validation_feedback: list[SignalValidationFeedback] = Field(default_factory=list)
    constraints: SignalDiscoveryConstraints = Field(default_factory=SignalDiscoveryConstraints)

    @model_validator(mode="after")
    def _validate_data_inputs(self) -> "SignalDiscoveryInput":
        if any(artifact.kind != "bars" for artifact in self.history_artifacts):
            raise ValueError("history_artifacts must only contain bars artifacts")
        return self


class SignalProposal(StrictSchema):
    proposal_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    features: list[SignalFeature] = Field(min_length=1)
    logic_spec: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    invalidation_conditions: list[str] = Field(min_length=1)
    minimum_history_bars: int = Field(ge=1)
    applicable_symbols: list[str] = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    parent_candidate_id: str | None = None
    revision_summary: str | None = Field(default=None, min_length=1, max_length=4_000)

    @field_validator("applicable_symbols")
    @classmethod
    def _normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = [value.upper() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("applicable_symbols must be non-empty and unique")
        return normalized

    def requires_news_evidence(self) -> bool:
        return any(feature.source in {"news", "event"} for feature in self.features)

    def validate_discovery_input(self, discovery_input: SignalDiscoveryInput) -> None:
        """Enforce the extra evidence requirement for news- or event-driven signals."""
        if self.requires_news_evidence() and not discovery_input.news_evidence:
            raise ValueError("news-driven signal proposals require news_evidence")
        if self.requires_news_evidence() and not discovery_input.constraints.allow_news_features:
            raise ValueError("news-driven signal proposals are disabled by constraints")
        if discovery_input.validation_feedback:
            parent_candidate_id = discovery_input.validation_feedback[-1].candidate_id
            if self.parent_candidate_id != parent_candidate_id or self.revision_summary is None:
                raise ValueError("revised signal proposals require parent_candidate_id and revision_summary")


class CandidateFunction(StrictSchema):
    candidate_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    interface_version: str = Field(min_length=1)
    source_artifact: ArtifactRef
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_source(self) -> "CandidateFunction":
        if self.source_artifact.kind != "candidate_source":
            raise ValueError("source_artifact must have kind='candidate_source'")
        if self.source_artifact.sha256 != self.source_hash:
            raise ValueError("source_hash must equal source_artifact.sha256")
        return self


class ValidationSplitResult(StrictSchema):
    split_name: Literal["discovery", "validation", "holdout"]
    time_window: TimeWindow
    sample_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    deterministic: bool
    error_rate: float = Field(ge=0, le=1)


class LeakageCheck(StrictSchema):
    name: str = Field(min_length=1)
    passed: bool
    details: str = Field(min_length=1)


class StabilityResult(StrictSchema):
    passed: bool
    coverage: float = Field(ge=0, le=1)
    cross_symbol_consistency: float | None = Field(default=None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class SignalValidationResult(StrictSchema):
    validation_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    dataset_refs: list[ArtifactRef] = Field(min_length=1)
    split_results: list[ValidationSplitResult] = Field(min_length=1)
    leakage_checks: list[LeakageCheck] = Field(min_length=1)
    stability: StabilityResult
    limitations: list[str] = Field(default_factory=list)
    decision: ValidationDecision
    metrics_artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def _validate_decision(self) -> "SignalValidationResult":
        if any(reference.kind != "bars" for reference in self.dataset_refs):
            raise ValueError("dataset_refs must only contain bars artifacts")
        if self.metrics_artifact is not None and self.metrics_artifact.kind != "validation_metrics":
            raise ValueError("metrics_artifact must have kind='validation_metrics'")
        if self.decision == "pass" and not all(check.passed for check in self.leakage_checks):
            raise ValueError("a passing validation result requires all leakage checks to pass")
        return self


class SignalVersion(StrictSchema):
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: SignalVersionStatus
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_id: str = Field(min_length=1)
    approved_by: str | None = None
    approved_at: datetime | None = None

    @field_validator("approved_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _validate_approval(self) -> "SignalVersion":
        if self.status == "active" and (self.approved_by is None or self.approved_at is None):
            raise ValueError("an active signal version requires an approver and approval time")
        return self


class SignalApproval(StrictSchema):
    """A human approval record; agents never create an approved record themselves."""

    approval_id: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    decision: Literal["approved", "rejected"]
    decided_by: str = Field(min_length=1)
    reason: str | None = None
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class SignalObservation(StrictSchema):
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    symbol: str = Field(min_length=1)
    timestamp: datetime
    label: SignalLabel
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

__all__ = [
    "CandidateFunction",
    "ExistingSignal",
    "LeakageCheck",
    "SignalDiscoveryConstraints",
    "SignalDiscoveryInput",
    "SignalFeature",
    "SignalApproval",
    "SignalLabel",
    "SignalObservation",
    "SignalProposal",
    "SignalValidationFeedback",
    "SignalValidationResult",
    "SignalVersion",
    "SignalVersionStatus",
    "StabilityResult",
    "ValidationDecision",
    "ValidationSplitResult",
]
