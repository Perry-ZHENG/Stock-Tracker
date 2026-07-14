"""Report claims, validation results, drafts, and final report contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.contracts.evidence import EvidenceRef

ClaimType = Literal["fact", "function_output", "inference", "unknown"]
ReportStatus = Literal["draft", "needs_revision", "rejected", "final"]


class ReportClaim(StrictSchema):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    claim_type: ClaimType
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class ReportSection(StrictSchema):
    title: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    content: str = Field(min_length=1)


class ClaimValidationResult(StrictSchema):
    claim_id: str = Field(min_length=1)
    status: Literal["passed", "needs_revision", "rejected"]
    issues: list[str] = Field(default_factory=list)


class ReportValidationResult(StrictSchema):
    status: Literal["passed", "needs_revision", "rejected"]
    claim_results: list[ClaimValidationResult] = Field(min_length=1)
    missing_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_overall_status(self) -> "ReportValidationResult":
        statuses = {result.status for result in self.claim_results}
        if "rejected" in statuses and self.status != "rejected":
            raise ValueError("a rejected claim requires a rejected report validation")
        if "needs_revision" in statuses and self.status == "passed":
            raise ValueError("a revisable claim cannot produce a passed report validation")
        return self


class ReportDraft(StrictSchema):
    draft_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    sections: list[ReportSection] = Field(min_length=1)
    claims: list[ReportClaim] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_section_claims(self) -> "ReportDraft":
        known_claims = {claim.claim_id for claim in self.claims}
        unknown_claims = {
            claim_id
            for section in self.sections
            for claim_id in section.claim_ids
            if claim_id not in known_claims
        }
        if unknown_claims:
            raise ValueError(f"sections reference unknown claims: {sorted(unknown_claims)}")
        return self


class FinalReport(StrictSchema):
    report_id: str = Field(min_length=1)
    draft: ReportDraft
    validation: ReportValidationResult
    status: Literal["final"] = "final"
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def _normalize_datetime(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_final_report(self) -> "FinalReport":
        if self.validation.status != "passed":
            raise ValueError("a FinalReport requires a passed validation result")
        return self


__all__ = [
    "ClaimType",
    "ClaimValidationResult",
    "FinalReport",
    "ReportClaim",
    "ReportDraft",
    "ReportSection",
    "ReportStatus",
    "ReportValidationResult",
]
