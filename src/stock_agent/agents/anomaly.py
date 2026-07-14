"""Evidence-gated anomaly analysis with candidate explanations, not causal assertions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.analysis import AnalysisMetric, AnomalyAnalysis, CandidateCause
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import ArtifactRef, DataEvidence, EvidenceGapRequest, EvidenceRef, NewsEvidence
from stock_agent.evidence.service import EvidenceService
from stock_agent.research.anomaly_metrics import AnomalyThresholds, calculate_anomaly_metrics
from stock_agent.schemas import Bar


class AnomalyAnalysisInput(StrictSchema):
    data_evidence: DataEvidence
    history_artifact: ArtifactRef
    news_evidence: list[NewsEvidence] = Field(default_factory=list)
    benchmark_evidence: DataEvidence | None = None
    peer_evidence: list[DataEvidence] = Field(default_factory=list)
    thresholds: AnomalyThresholds = Field(default_factory=AnomalyThresholds)
    require_cause_evidence: bool = False


class AnomalyAnalysisAgent:
    """Classify data quality before assessing whether market movement is unusual."""

    def __init__(self, *, artifact_service: ArtifactService, prompt_path: Path | None = None) -> None:
        self.artifact_service = artifact_service
        self.evidence_service = EvidenceService(artifact_service.store.connection, artifact_service.store)
        self.prompt_path = prompt_path or Path(__file__).with_name("prompts") / "anomaly.md"

    def analyze(
        self,
        task_id: str,
        analysis_input: AnomalyAnalysisInput,
        *,
        analysis_id: str,
        now: datetime | None = None,
    ) -> AnomalyAnalysis | EvidenceGapRequest:
        active_now = _utc_now(now)
        evidence_issue = self._verify_input(task_id, analysis_input, now=active_now)
        if evidence_issue is not None:
            return evidence_issue
        try:
            current_bars = _bars_from_payload(self.artifact_service.load_json(task_id, analysis_input.data_evidence.bar_artifact))
            history_bars = _bars_from_payload(self.artifact_service.load_json(task_id, analysis_input.history_artifact))
            benchmark_bars = (
                _bars_from_payload(self.artifact_service.load_json(task_id, analysis_input.benchmark_evidence.bar_artifact))
                if analysis_input.benchmark_evidence is not None
                else None
            )
            values = calculate_anomaly_metrics(
                current_bars,
                history_bars,
                thresholds=analysis_input.thresholds,
                benchmark_bars=benchmark_bars,
            )
        except (ValueError, KeyError):
            return _gap(task_id, ["bar"], "current or historical bar evidence is insufficient for anomaly metrics")

        base_refs = list(analysis_input.data_evidence.evidence_refs)
        benchmark_refs = (
            list(analysis_input.benchmark_evidence.evidence_refs)
            if analysis_input.benchmark_evidence is not None
            else []
        )
        metrics = [
            AnalysisMetric(name="price_return", value=values.price_return, baseline=analysis_input.thresholds.price_return_threshold, evidence_refs=base_refs),
            AnalysisMetric(name="volume_ratio", value=values.volume_ratio, baseline=analysis_input.thresholds.volume_ratio_threshold, evidence_refs=base_refs),
            AnalysisMetric(name="realized_volatility", value=values.realized_volatility, baseline=analysis_input.thresholds.volatility_threshold, evidence_refs=base_refs),
        ]
        if values.benchmark_relative_return is not None:
            metrics.append(
                AnalysisMetric(
                    name="benchmark_relative_return",
                    value=values.benchmark_relative_return,
                    baseline=analysis_input.thresholds.price_return_threshold,
                    evidence_refs=base_refs,
                )
            )

        quality_flags = analysis_input.data_evidence.quality.flags
        if analysis_input.data_evidence.quality.status != "normal" or any(
            flag.startswith(("provider_compare", "provider_fallback", "invalid_bars", "quarantined_bars"))
            for flag in quality_flags
        ):
            return AnomalyAnalysis(
                analysis_id=analysis_id,
                metrics=metrics,
                baseline="classification=data_quality_anomaly; market interpretation suppressed",
                candidate_causes=[],
                counter_evidence=base_refs,
                unknowns=["data_quality_anomaly", *sorted(quality_flags)],
                confidence=0.2,
                evidence_refs=base_refs,
                created_at=active_now,
            )
        if not values.market_anomaly:
            return AnomalyAnalysis(
                analysis_id=analysis_id,
                metrics=metrics,
                baseline="classification=normal_variation; no configured market threshold was exceeded",
                candidate_causes=[],
                counter_evidence=base_refs,
                unknowns=["no_market_anomaly"],
                confidence=0.7,
                evidence_refs=base_refs,
                created_at=active_now,
            )

        news_refs = [reference for evidence in analysis_input.news_evidence for reference in evidence.evidence_refs]
        if analysis_input.require_cause_evidence and not news_refs:
            return _gap(task_id, ["news"], "market anomaly needs contemporaneous news evidence before cause research")
        candidate_causes: list[CandidateCause] = []
        unknowns: list[str] = ["causality_not_established"]
        if news_refs:
            candidate_causes.append(
                CandidateCause(
                    description="Contemporaneous news may be a candidate explanation; timing alone does not establish causality.",
                    support_evidence=news_refs,
                    counter_evidence=base_refs,
                    confidence=0.25,
                )
            )
        peer_refs = [reference for evidence in analysis_input.peer_evidence for reference in evidence.evidence_refs]
        if peer_refs:
            candidate_causes.append(
                CandidateCause(
                    description="Peer or industry evidence may indicate a synchronous move; the scope remains unconfirmed.",
                    support_evidence=peer_refs,
                    counter_evidence=base_refs,
                    confidence=0.35,
                )
            )
        all_refs = _unique_refs([*base_refs, *benchmark_refs, *news_refs, *peer_refs])
        return AnomalyAnalysis(
            analysis_id=analysis_id,
            metrics=metrics,
            baseline=f"classification=market_anomaly; triggers={','.join(values.triggers)}",
            candidate_causes=candidate_causes,
            counter_evidence=base_refs,
            unknowns=unknowns,
            confidence=0.45 if candidate_causes else 0.3,
            evidence_refs=all_refs,
            created_at=active_now,
        )

    def _verify_input(
        self,
        task_id: str,
        analysis_input: AnomalyAnalysisInput,
        *,
        now: datetime,
    ) -> EvidenceGapRequest | None:
        try:
            self.artifact_service.open_bytes(task_id, analysis_input.history_artifact)
            self.artifact_service.open_bytes(task_id, analysis_input.data_evidence.bar_artifact)
            if analysis_input.benchmark_evidence is not None:
                self.artifact_service.open_bytes(task_id, analysis_input.benchmark_evidence.bar_artifact)
            for peer in analysis_input.peer_evidence:
                self.artifact_service.open_bytes(task_id, peer.bar_artifact)
            all_refs = [
                *analysis_input.data_evidence.evidence_refs,
                *(
                    analysis_input.benchmark_evidence.evidence_refs
                    if analysis_input.benchmark_evidence is not None
                    else []
                ),
                *(reference for evidence in analysis_input.news_evidence for reference in evidence.evidence_refs),
                *(reference for evidence in analysis_input.peer_evidence for reference in evidence.evidence_refs),
            ]
            for reference in all_refs:
                if self.evidence_service.get(task_id, reference.evidence_id, now=now) != reference:
                    return _gap(task_id, ["bar"], "input evidence reference does not match stored task evidence")
        except Exception:
            return _gap(task_id, ["bar"], "input evidence or artifact is unavailable")
        return None


def _bars_from_payload(payload: object) -> list[Bar]:
    if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
        raise ValueError("bar artifact payload is invalid")
    return [Bar.model_validate(item) for item in payload["bars"]]


def _gap(task_id: str, evidence_types: list[str], reason: str) -> EvidenceGapRequest:
    return EvidenceGapRequest(
        task_id=task_id,
        requester="anomaly_analysis",
        missing_evidence_types=evidence_types,
        reason=reason,
    )


def _unique_refs(references: list[EvidenceRef]) -> list[EvidenceRef]:
    values = {reference.evidence_id: reference for reference in references}
    return [values[key] for key in sorted(values)]


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("anomaly analysis time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["AnomalyAnalysisAgent", "AnomalyAnalysisInput"]
