"""Strict macro evidence and LLM-draft contracts for grounded macro analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.contracts.evidence import EvidenceRef

MacroEvidenceKind = Literal[
    "policy",
    "rate",
    "inflation",
    "growth",
    "index",
    "cross_asset",
    "commodity",
    "currency",
    "other",
]
MacroStance = Literal["supportive", "restrictive", "neutral", "mixed", "unknown"]


class MacroEvidenceItem(StrictSchema):
    """One verified macro event or indicator obtained through an allowlisted source."""

    event_id: str = Field(min_length=1)
    kind: MacroEvidenceKind
    stance: MacroStance = "unknown"
    description: str = Field(min_length=1, max_length=4_000)
    occurred_at: datetime
    evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class MacroPathDraft(StrictSchema):
    """Model-proposed, evidence-id-only path before references are resolved."""

    event_id: str = Field(min_length=1)
    intermediate_variable: str = Field(min_length=1, max_length=500)
    affected_scope: str = Field(min_length=1, max_length=500)
    expected_window: str = Field(min_length=1, max_length=240)
    evidence_ids: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1, max_length=8)
    uncertainties: list[str] = Field(min_length=1, max_length=8)
    falsification_conditions: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)


class MacroScenarioDraft(StrictSchema):
    name: Literal["base", "alternative"]
    description: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1)


class MacroReasoningDraft(StrictSchema):
    paths: list[MacroPathDraft] = Field(min_length=1, max_length=12)
    scenarios: list[MacroScenarioDraft] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def _require_base_and_alternative(self) -> "MacroReasoningDraft":
        names = {scenario.name for scenario in self.scenarios}
        if names != {"base", "alternative"}:
            raise ValueError("macro reasoning requires one base and one alternative scenario")
        if len(names) != len(self.scenarios):
            raise ValueError("macro reasoning scenarios must not duplicate a name")
        return self


def has_conflicting_stances(items: list[MacroEvidenceItem]) -> bool:
    """Treat simultaneous supportive and restrictive indicators as an explicit conflict."""

    stances = {item.stance for item in items}
    return "supportive" in stances and "restrictive" in stances


__all__ = [
    "MacroEvidenceItem",
    "MacroEvidenceKind",
    "MacroPathDraft",
    "MacroReasoningDraft",
    "MacroScenarioDraft",
    "MacroStance",
    "has_conflicting_stances",
]
