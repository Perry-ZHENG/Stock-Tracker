from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.bars import generate_bar_id
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import DataEvidence, DataEvidenceRequest
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.providers.base import MarketDataProvider
from stock_agent.providers.registry import ProviderRegistry
from stock_agent.research.data_evidence import DataEvidenceFailure, DataEvidenceWorkflow, V2_READ_ONLY_PROVIDER_NAMES
from stock_agent.schemas import Bar
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(
    from_ts=datetime(2026, 5, 22, 13, 30, tzinfo=UTC),
    to_ts=NOW,
    timezone="America/New_York",
)


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar] | None = None, *, error: str | None = None) -> None:
        self.bars = bars or []
        self.error = error

    def fetch_intraday_bars(self, **_kwargs: object) -> list[Bar]:
        if self.error:
            raise RuntimeError(self.error)
        return self.bars

    def fetch_provider_health(self) -> dict[str, str]:
        return {"provider": "fake", "status": "healthy"}


def test_data_evidence_writes_replayable_bars_and_is_content_stable(tmp_path: Path) -> None:
    connection = _connection_with_task(tmp_path)
    workflow = _workflow(tmp_path, connection, primary=_bars())

    first = workflow.collect("task-data", _request(), now=NOW)
    second = workflow.collect("task-data", _request(), now=NOW)

    assert isinstance(first, DataEvidence)
    assert isinstance(second, DataEvidence)
    assert first.bar_artifact == second.bar_artifact
    assert first.quality.status == "normal"
    assert {feature.name for feature in first.features} == {
        "QQQ.return_change",
        "QQQ.volume_ratio",
        "QQQ.realized_volatility",
        "QQQ.gap",
        "QQQ.relative_to_baseline",
    }
    stored = workflow.artifact_service.load_json("task-data", first.bar_artifact)
    assert len(stored["bars"]) == 5
    assert first.evidence_refs[0].artifact_id == first.bar_artifact.artifact_id
    assert all(reference.provider_name != "broker_market_data" for reference in first.provider_refs)
    connection.close()


def test_data_evidence_reports_csv_fallback_and_quality_degradation(tmp_path: Path) -> None:
    connection = _connection_with_task(tmp_path)
    workflow = _workflow(
        tmp_path,
        connection,
        primary=[],
        primary_error="twelve data unavailable",
        fallback=_bars(),
    )

    result = workflow.collect("task-data", _request(), now=NOW)

    assert isinstance(result, DataEvidence)
    assert result.provider_refs[0].provider_name == "csv_demo"
    assert result.provider_refs[0].fallback_used is True
    assert "provider_fallback_used" in result.quality.flags
    connection.close()


def test_data_evidence_quarantines_invalid_and_gap_bars_without_hiding_degradation(tmp_path: Path) -> None:
    connection = _connection_with_task(tmp_path)
    malformed = _bar(datetime(2026, 5, 22, 14, 0, tzinfo=UTC), high=99, low=100)
    gapped = _bar(datetime(2026, 5, 22, 14, 30, tzinfo=UTC))
    workflow = _workflow(tmp_path, connection, primary=[_bar(WINDOW.from_ts), malformed, gapped])

    result = workflow.collect("task-data", _request(features=[]), now=NOW)

    assert isinstance(result, DataEvidence)
    assert result.quality.status == "degraded"
    assert result.quality.quarantined_bar_count >= 2
    assert any(flag.startswith("invalid_bars:") for flag in result.quality.flags)
    assert any(flag.startswith("quarantined_bars:") for flag in result.quality.flags)
    connection.close()


def test_data_evidence_records_cross_provider_conflicts(tmp_path: Path) -> None:
    connection = _connection_with_task(tmp_path)
    primary = _bars()
    secondary = [bar.model_copy(update={"close": bar.close * 1.02, "high": bar.high * 1.02}) for bar in _bars()]
    workflow = _workflow(
        tmp_path,
        connection,
        primary=primary,
        fallback=secondary,
        comparison_provider_name="csv_demo",
    )

    result = workflow.collect("task-data", _request(), now=NOW)

    assert isinstance(result, DataEvidence)
    assert "provider_compare_unhealthy" in result.quality.flags
    assert any("provider_compare_unhealthy" in bar["quality_flag"] for bar in workflow.artifact_service.load_json("task-data", result.bar_artifact)["bars"])
    trace = connection.execute("SELECT module FROM trace_chain WHERE module = 'provider_compare'").fetchone()
    assert trace is not None
    connection.close()


def test_data_evidence_returns_controlled_failures_for_closed_or_empty_windows(tmp_path: Path) -> None:
    connection = _connection_with_task(tmp_path)
    workflow = _workflow(tmp_path, connection, primary=[])
    weekend = DataEvidenceRequest(
        symbols=["QQQ"],
        time_window=TimeWindow(
            from_ts=datetime(2026, 5, 23, 13, 30, tzinfo=UTC),
            to_ts=datetime(2026, 5, 23, 20, 0, tzinfo=UTC),
            timezone="America/New_York",
        ),
    )

    closed = workflow.collect("task-data", weekend, now=NOW)
    empty = workflow.collect("task-data", _request(), now=NOW)

    assert isinstance(closed, DataEvidenceFailure) and closed.code == "market_closed"
    assert isinstance(empty, DataEvidenceFailure) and empty.code == "empty_result"
    connection.close()


def _workflow(
    root: Path,
    connection: object,
    *,
    primary: list[Bar],
    primary_error: str | None = None,
    fallback: list[Bar] | None = None,
    comparison_provider_name: str | None = None,
) -> DataEvidenceWorkflow:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = "twelve_data"
    config["provider"]["priority"] = ["twelve_data"]
    config["provider"]["fallback"] = {"enabled": fallback is not None, "order": ["csv_demo"]}
    registry = ProviderRegistry(
        root=root,
        config=validate_config(config),
        connection=connection,  # type: ignore[arg-type]
        provider_factories={
            "twelve_data": lambda: FakeProvider(primary, error=primary_error),
            "csv_demo": lambda: FakeProvider(fallback or []),
        },
        allowed_provider_names=V2_READ_ONLY_PROVIDER_NAMES,
    )
    return DataEvidenceWorkflow(
        root=root,
        connection=connection,  # type: ignore[arg-type]
        provider_registry=registry,
        comparison_provider_name=comparison_provider_name,
    )


def _connection_with_task(tmp_path: Path):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-data",
            request=ResearchRequest(
                request_id="request-data",
                question="Collect market data evidence.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return connection


def _request(*, features: list[str] | None = None) -> DataEvidenceRequest:
    return DataEvidenceRequest(
        symbols=["QQQ"],
        time_window=WINDOW,
        interval="30m",
        features=features if features is not None else [
            "return_change",
            "volume_ratio",
            "realized_volatility",
            "gap",
            "relative_to_baseline",
        ],
        baseline_window=2,
        freshness_seconds=60 * 60 * 8,
    )


def _bars() -> list[Bar]:
    return [
        _bar(WINDOW.from_ts + timedelta(minutes=30 * index), close=100 + index, volume=1000 + index * 100)
        for index in range(5)
    ]


def _bar(
    timestamp: datetime,
    *,
    high: float = 102,
    low: float = 99,
    close: float = 100,
    volume: int = 1000,
) -> Bar:
    timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
    effective_high = high if high < low else max(high, close + 1)
    return Bar(
        bar_id=generate_bar_id("QQQ", "30m", timestamp_text, "fake"),
        symbol="QQQ",
        timestamp=timestamp,
        interval="30m",
        open=100,
        high=effective_high,
        low=low,
        close=close,
        volume=volume,
        vwap=100,
        source="fake",
    )
