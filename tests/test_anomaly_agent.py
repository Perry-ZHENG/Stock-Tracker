from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.agents.anomaly import AnomalyAnalysisAgent, AnomalyAnalysisInput
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.bars.validation import generate_bar_id
from stock_agent.contracts.analysis import AnomalyAnalysis
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import (
    DataEvidence,
    DataQuality,
    EvidenceGapRequest,
    NewsCoverage,
    NewsEvidence,
    ProviderReference,
)
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.research.anomaly_metrics import AnomalyThresholds
from stock_agent.schemas import Bar
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 3, 3, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=10), to_ts=NOW, timezone="America/New_York")


def test_anomaly_agent_classifies_market_anomaly_with_candidate_evidence(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(tmp_path, include_news=True, include_peer=True)

    result = agent.analyze("task-anomaly", analysis_input, analysis_id="analysis-1", now=NOW)

    assert isinstance(result, AnomalyAnalysis)
    assert result.baseline.startswith("classification=market_anomaly")
    assert {metric.name for metric in result.metrics} == {"price_return", "volume_ratio", "realized_volatility"}
    assert len(result.candidate_causes) == 2
    assert result.unknowns == ["causality_not_established"]
    assert all("does not establish causality" in cause.description.lower() or "scope remains unconfirmed" in cause.description.lower() for cause in result.candidate_causes)
    connection.close()


def test_anomaly_agent_requests_news_when_cause_evidence_is_required(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(tmp_path)
    required_input = analysis_input.model_copy(update={"require_cause_evidence": True})

    result = agent.analyze("task-anomaly", required_input, analysis_id="analysis-2", now=NOW)

    assert isinstance(result, EvidenceGapRequest)
    assert result.requester == "anomaly_analysis"
    assert result.missing_evidence_types == ["news"]
    connection.close()


def test_anomaly_agent_classifies_normal_variation_without_candidate_causes(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(
        tmp_path,
        current_closes=[100, 100.5],
        current_volumes=[1_000, 1_000],
    )

    result = agent.analyze("task-anomaly", analysis_input, analysis_id="analysis-normal", now=NOW)

    assert isinstance(result, AnomalyAnalysis)
    assert result.baseline.startswith("classification=normal_variation")
    assert result.candidate_causes == []
    assert result.unknowns == ["no_market_anomaly"]
    connection.close()


def test_anomaly_agent_suppresses_market_interpretation_for_degraded_data(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(tmp_path, quality=DataQuality(status="degraded", flags=["provider_compare_unhealthy"]))

    result = agent.analyze("task-anomaly", analysis_input, analysis_id="analysis-3", now=NOW)

    assert isinstance(result, AnomalyAnalysis)
    assert result.baseline == "classification=data_quality_anomaly; market interpretation suppressed"
    assert result.candidate_causes == []
    assert result.confidence == 0.2
    connection.close()


def test_anomaly_agent_marks_invalid_bars_as_data_quality_anomaly(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(tmp_path, quality=DataQuality(flags=["invalid_bars:1"]))

    result = agent.analyze("task-anomaly", analysis_input, analysis_id="analysis-invalid", now=NOW)

    assert isinstance(result, AnomalyAnalysis)
    assert result.baseline.startswith("classification=data_quality_anomaly")
    assert "invalid_bars:1" in result.unknowns
    connection.close()


def test_anomaly_agent_requests_bar_evidence_for_insufficient_history(tmp_path: Path) -> None:
    connection, agent, analysis_input = _input(tmp_path, history_count=3)

    result = agent.analyze("task-anomaly", analysis_input, analysis_id="analysis-4", now=NOW)

    assert isinstance(result, EvidenceGapRequest)
    assert result.missing_evidence_types == ["bar"]
    connection.close()


def _input(
    tmp_path: Path,
    *,
    include_news: bool = False,
    include_peer: bool = False,
    quality: DataQuality | None = None,
    history_count: int = 6,
    current_closes: list[float] | None = None,
    current_volumes: list[int] | None = None,
) -> tuple[object, AnomalyAnalysisAgent, AnomalyAnalysisInput]:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-anomaly",
            request=ResearchRequest(
                request_id="request-anomaly",
                question="Explain an unusual move without issuing a trade recommendation.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    evidence_service = EvidenceService(connection, service.store)
    current_artifact = _bars_artifact(
        service,
        "current",
        current_closes or [100, 105],
        current_volumes or [1_000, 5_000],
    )
    history_artifact = _bars_artifact(service, "history", [100, 100.3, 100.1, 100.4, 100.2, 100.5][:history_count], [1_000] * history_count)
    data_ref = evidence_service.create(
        "task-anomaly",
        artifact=current_artifact,
        evidence_type="bar",
        source="fixture-current",
        observed_at=NOW,
        evidence_id="evidence-current",
    )
    data_evidence = _data_evidence(current_artifact, data_ref, quality or DataQuality())
    news_evidence: list[NewsEvidence] = []
    if include_news:
        news_artifact = service.save_json(
            "task-anomaly",
            kind="news_body",
            payload={"headline": "Illustrative contemporaneous release", "published_at": NOW.isoformat()},
            source="fixture-news",
            created_at=NOW,
        )
        news_ref = evidence_service.create(
            "task-anomaly",
            artifact=news_artifact,
            evidence_type="news",
            source="fixture-news",
            observed_at=NOW,
            evidence_id="evidence-news",
        )
        news_evidence = [
            NewsEvidence(
                request={"symbols": ["QQQ"], "time_window": WINDOW},
                source_count=1,
                coverage=NewsCoverage(requested_symbol_count=1, covered_symbol_count=1, source_count=1),
                artifact_refs=[news_artifact],
                evidence_refs=[news_ref],
            )
        ]
    peer_evidence: list[DataEvidence] = []
    if include_peer:
        peer_artifact = _bars_artifact(service, "peer", [50, 52], [1_000, 2_000], symbol="XLK")
        peer_ref = evidence_service.create(
            "task-anomaly",
            artifact=peer_artifact,
            evidence_type="bar",
            source="fixture-peer",
            observed_at=NOW,
            evidence_id="evidence-peer",
        )
        peer_evidence = [_data_evidence(peer_artifact, peer_ref, DataQuality(), symbol="XLK")]
    return (
        connection,
        AnomalyAnalysisAgent(artifact_service=service),
        AnomalyAnalysisInput(
            data_evidence=data_evidence,
            history_artifact=history_artifact,
            news_evidence=news_evidence,
            peer_evidence=peer_evidence,
            thresholds=AnomalyThresholds(
                price_return_threshold=0.02,
                volume_ratio_threshold=1.5,
                volatility_threshold=0.05,
                min_baseline_bars=5,
            ),
        ),
    )


def _bars_artifact(
    service: ArtifactService,
    source: str,
    closes: list[float],
    volumes: list[int],
    *,
    symbol: str = "QQQ",
):
    bars = [_bar(symbol, index, close, volume, source) for index, (close, volume) in enumerate(zip(closes, volumes))]
    return service.save_json(
        "task-anomaly",
        kind="bars",
        payload={"bars": [bar.model_dump(mode="json") for bar in bars]},
        source=f"fixture-{source}",
        created_at=NOW,
    )


def _bar(symbol: str, index: int, close: float, volume: int, source: str) -> Bar:
    timestamp = NOW - timedelta(days=10 - index)
    iso_timestamp = timestamp.isoformat().replace("+00:00", "Z")
    return Bar(
        bar_id=generate_bar_id(symbol, "1d", iso_timestamp, f"fixture-{source}"),
        symbol=symbol,
        timestamp=timestamp,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        source=f"fixture-{source}",
    )


def _data_evidence(artifact, evidence_ref, quality: DataQuality, *, symbol: str = "QQQ") -> DataEvidence:
    return DataEvidence(
        request={"symbols": [symbol], "time_window": WINDOW, "interval": "1d"},
        bar_artifact=artifact,
        summary=f"Verified {symbol} fixture bars.",
        quality=quality,
        provider_refs=[ProviderReference(provider_name="fixture", request_id=f"request-{symbol}", observed_at=NOW)],
        evidence_refs=[evidence_ref],
    )
