"""Contracts produced by anomaly and macro analysis agents."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.contracts.evidence import EvidenceRef


class AnalysisMetric(StrictSchema):
    name: str = Field(min_length=1)
    value: float
    baseline: float | None = None
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class CandidateCause(StrictSchema):
    description: str = Field(min_length=1)
    support_evidence: list[EvidenceRef] = Field(default_factory=list)
    counter_evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class AnomalyAnalysis(StrictSchema):
    analysis_id: str = Field(min_length=1)
    metrics: list[AnalysisMetric] = Field(min_length=1)
    baseline: str = Field(min_length=1)
    candidate_causes: list[CandidateCause] = Field(default_factory=list)
    counter_evidence: list[EvidenceRef] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class MacroEvent(StrictSchema):
    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    occurred_at: datetime
    evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class TransmissionPath(StrictSchema):
    event_id: str = Field(min_length=1)
    intermediate_variable: str = Field(min_length=1)
    affected_scope: str = Field(min_length=1)
    expected_window: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    falsification_conditions: list[str] = Field(default_factory=list)


class MacroScenario(StrictSchema):
    name: Literal["base", "alternative"]
    description: str = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class MacroAnalysis(StrictSchema):
    analysis_id: str = Field(min_length=1)
    events: list[MacroEvent] = Field(min_length=1)
    transmission_paths: list[TransmissionPath] = Field(min_length=1)
    affected_scope: list[str] = Field(min_length=1)
    alternative_scenarios: list[MacroScenario] = Field(min_length=2)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


__all__ = [
    "AnalysisMetric",
    "AnomalyAnalysis",
    "CandidateCause",
    "MacroAnalysis",
    "MacroEvent",
    "MacroScenario",
    "TransmissionPath",
]
