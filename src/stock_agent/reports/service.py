"""The sole V2 path from report inputs to a validated FinalReport artifact."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from stock_agent.agents.report import ReportAgent, ReportEvidenceGap, ReportInput
from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import ArtifactRef, EvidenceGapRequest
from stock_agent.contracts.reports import FinalReport, ReportDraft, ReportValidationResult
from stock_agent.reports.bundle import ReportBundleBuilder, ReportBundleError, ReportBundleRequest
from stock_agent.reports.renderers import ReportRenderFormat, render_report
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.validation.report import ReportValidator


class ReportPolicy(StrictSchema):
    max_revisions: int = Field(default=2, ge=0, le=10)
    output_formats: list[ReportRenderFormat] = Field(default_factory=lambda: ["json", "markdown"])


class ReportPublicationResult(StrictSchema):
    status: str
    final_report: FinalReport | None = None
    draft: ReportDraft | None = None
    validation: ReportValidationResult | None = None
    evidence_gap: EvidenceGapRequest | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class ReportService:
    """Draft, validate, finalise, and render reports without a Draft publication bypass."""

    def __init__(
        self,
        *,
        bundle_builder: ReportBundleBuilder,
        report_agent: ReportAgent,
        validator: ReportValidator,
        repository: ReportRepository,
        artifact_service: ArtifactService,
    ) -> None:
        self.bundle_builder = bundle_builder
        self.report_agent = report_agent
        self.validator = validator
        self.repository = repository
        self.artifact_service = artifact_service

    def publish(
        self,
        request: ReportBundleRequest,
        *,
        policy: ReportPolicy | None = None,
        revision: int = 0,
        known_conflicts: list[str] | None = None,
        limitations: list[str] | None = None,
        now: datetime | None = None,
    ) -> ReportPublicationResult:
        active_now = _utc_now(now)
        active_policy = policy or ReportPolicy()
        if revision > active_policy.max_revisions:
            return ReportPublicationResult(status="rejected", evidence_gap=_gap(request.task_id, "report revision budget is exhausted"))
        try:
            assembled = self.bundle_builder.build(request, now=active_now)
        except ReportBundleError as exc:
            return ReportPublicationResult(status="needs_revision", evidence_gap=_gap(request.task_id, str(exc)))
        draft_or_gap = self.report_agent.draft(
            ReportInput(
                task_id=request.task_id,
                request=request.request,
                evidence_bundle=assembled.bundle,
                signal_observations=assembled.signal_observations,
                anomaly_analysis=assembled.anomaly_analysis,
                macro_analysis=assembled.macro_analysis,
                known_conflicts=known_conflicts or [],
                limitations=limitations or [],
            ),
            draft_id=f"draft-{request.task_id}-{uuid4().hex}",
            now=active_now,
        )
        if isinstance(draft_or_gap, EvidenceGapRequest):
            return ReportPublicationResult(status="needs_revision", evidence_gap=draft_or_gap)
        draft = draft_or_gap
        self.repository.save_draft(draft)
        validation = self.validator.validate(draft, assembled.bundle, now=active_now, known_conflicts=known_conflicts)
        self._save_validation_artifact(request.task_id, draft, validation, now=active_now)
        if validation.status == "rejected":
            return ReportPublicationResult(status="rejected", draft=draft, validation=validation)
        if validation.status != "passed":
            if revision >= active_policy.max_revisions:
                return ReportPublicationResult(status="rejected", draft=draft, validation=validation, evidence_gap=_gap(request.task_id, "report revision budget is exhausted"))
            return ReportPublicationResult(status="needs_revision", draft=draft, validation=validation, evidence_gap=_gap(request.task_id, "report validation requires evidence revision"))
        final = self.validator.create_final(report_id=f"report-{request.task_id}-{uuid4().hex}", draft=draft, validation=validation, published_at=active_now)
        self.repository.save_final(final)
        artifacts = [self._render_artifact(final, output_format, now=active_now) for output_format in active_policy.output_formats]
        return ReportPublicationResult(status="published", final_report=final, draft=draft, validation=validation, artifacts=artifacts)

    def _save_validation_artifact(self, task_id: str, draft: ReportDraft, validation: ReportValidationResult, *, now: datetime) -> ArtifactRef:
        return self.artifact_service.save_json(task_id, kind="report", payload={"draft_id": draft.draft_id, "validation": validation.model_dump(mode="json"), "model_version": "report-agent-v2"}, source="report_pipeline", created_at=now)

    def _render_artifact(self, report: FinalReport, output_format: ReportRenderFormat, *, now: datetime) -> ArtifactRef:
        media_type = "application/json" if output_format == "json" else "text/markdown"
        return self.artifact_service.save_bytes(report.draft.task_id, kind="report", payload=render_report(report, output_format), media_type=media_type, source=f"report_renderer:{output_format}", created_at=now)


def _gap(task_id: str, reason: str) -> EvidenceGapRequest:
    return EvidenceGapRequest(task_id=task_id, requester="report", missing_evidence_types=["analysis"], reason=reason)


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("report service time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["ReportPolicy", "ReportPublicationResult", "ReportService"]
