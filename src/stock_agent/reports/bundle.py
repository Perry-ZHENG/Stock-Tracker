"""Repository-backed assembly of the only EvidenceBundle a report may use."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import Field

from stock_agent.contracts.analysis import AnomalyAnalysis, MacroAnalysis
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import EvidenceBundle, EvidenceRef
from stock_agent.contracts.signals import SignalObservation
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.validation.evidence import EvidenceValidator


class ReportBundleRequest(StrictSchema):
    task_id: str = Field(min_length=1)
    request: ResearchRequest
    evidence_ids: list[str] = Field(min_length=1)
    signal_observations: list[SignalObservation] = Field(default_factory=list)
    anomaly_analysis_id: str | None = None
    macro_analysis_id: str | None = None


class ReportBundle(StrictSchema):
    bundle: EvidenceBundle
    signal_observations: list[SignalObservation] = Field(default_factory=list)
    anomaly_analysis: AnomalyAnalysis | None = None
    macro_analysis: MacroAnalysis | None = None


class ReportBundleError(ValueError):
    """Evidence, analysis, symbol, or time scope cannot support the report request."""


class ReportBundleBuilder:
    """Load only registered task evidence and validate its request scope before drafting."""

    def __init__(self, *, evidence_service: EvidenceService, report_repository: ReportRepository) -> None:
        self.evidence_service = evidence_service
        self.report_repository = report_repository
        self.evidence_validator = EvidenceValidator(evidence_service)

    def build(self, request: ReportBundleRequest, *, now: datetime | None = None) -> ReportBundle:
        active_now = _utc_now(now)
        try:
            references = [self.evidence_service.get(request.task_id, identifier, now=active_now) for identifier in request.evidence_ids]
            bundle = self.evidence_service.build_bundle(request.task_id, references, now=active_now)
        except Exception as exc:
            raise ReportBundleError("requested report evidence is unavailable") from exc
        self._validate_scope(request, bundle, references, now=active_now)
        anomaly = self._load_analysis(request.task_id, request.anomaly_analysis_id, AnomalyAnalysis, bundle)
        macro = self._load_analysis(request.task_id, request.macro_analysis_id, MacroAnalysis, bundle)
        for observation in request.signal_observations:
            _require_known_refs(observation.evidence_refs, bundle)
        return ReportBundle(
            bundle=bundle,
            signal_observations=request.signal_observations,
            anomaly_analysis=anomaly,
            macro_analysis=macro,
        )

    def _validate_scope(
        self,
        request: ReportBundleRequest,
        bundle: EvidenceBundle,
        references: list[EvidenceRef],
        *,
        now: datetime,
    ) -> None:
        materials, issues = self.evidence_validator.resolve(request.task_id, references, bundle, now=now)
        if issues:
            raise ReportBundleError("report evidence cannot be resolved: " + ",".join(issues))
        symbols = set(request.request.symbols)
        for material in materials:
            if material.symbols and not (material.symbols & symbols):
                raise ReportBundleError("evidence symbols do not match the research request")
            if any(
                timestamp < request.request.time_window.from_ts or timestamp > request.request.time_window.to_ts
                for timestamp in material.timestamps
            ):
                raise ReportBundleError("evidence timestamps fall outside the research request")

    def _load_analysis(self, task_id: str, analysis_id: str | None, expected_type, bundle: EvidenceBundle):
        if analysis_id is None:
            return None
        analysis = self.report_repository.get_analysis(analysis_id)
        if analysis is None or not isinstance(analysis, expected_type):
            raise ReportBundleError("requested analysis is unavailable or has the wrong type")
        _require_known_refs(analysis.evidence_refs, bundle)
        return analysis


def _require_known_refs(references: list[EvidenceRef], bundle: EvidenceBundle) -> None:
    known = {reference.evidence_id: reference for reference in bundle.evidence_refs}
    if any(known.get(reference.evidence_id) != reference for reference in references):
        raise ReportBundleError("analysis or signal references evidence outside the report bundle")


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("report bundle time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["ReportBundle", "ReportBundleBuilder", "ReportBundleError", "ReportBundleRequest"]
