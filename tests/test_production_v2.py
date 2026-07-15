from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from stock_agent.bars import generate_bar_id
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import EvidenceGapRequest
from stock_agent.contracts.tasks import ResearchConstraints, ResearchRequest
from stock_agent.news.providers import StaticNewsProvider
from stock_agent.news.service import NewsQueryService
from stock_agent.providers.base import MarketDataProvider
from stock_agent.providers.registry import ProviderRegistry
from stock_agent.research.data_evidence import DataEvidenceWorkflow, V2_READ_ONLY_PROVIDER_NAMES
from stock_agent.research.news_evidence import NewsEvidenceWorkflow
from stock_agent.schemas import Bar, NewsItem
from stock_agent.services.production_v2 import ProductionStepHandlerV2, build_production_v2
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.worker.research_v2 import ResearchTaskWorkerV2
from stock_agent.observability import AgentTraceRecorder


NOW = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(
    from_ts=datetime(2026, 5, 22, 13, 30, tzinfo=UTC),
    to_ts=NOW,
    timezone="America/New_York",
)


class FixtureProvider(MarketDataProvider):
    def fetch_intraday_bars(self, **_kwargs: object) -> list[Bar]:
        return [
            Bar(
                bar_id=generate_bar_id(
                    "QQQ",
                    "30m",
                    (WINDOW.from_ts + timedelta(minutes=30 * index)).isoformat().replace("+00:00", "Z"),
                    "fixture",
                ),
                symbol="QQQ",
                timestamp=WINDOW.from_ts + timedelta(minutes=30 * index),
                interval="30m",
                open=100 + index,
                high=102 + index,
                low=99 + index,
                close=101 + index,
                volume=1_000 + index * 100,
                vwap=100.5 + index,
                source="fixture",
            )
            for index in range(5)
        ]

    def fetch_provider_health(self) -> dict[str, str]:
        return {"status": "healthy"}


class FailOnceProvider(FixtureProvider):
    calls = 0

    def fetch_intraday_bars(self, **kwargs: object) -> list[Bar]:
        type(self).calls += 1
        if type(self).calls == 1:
            raise RuntimeError("fixture provider is temporarily unavailable")
        return super().fetch_intraday_bars(**kwargs)


class ReportModel:
    def __init__(self, connection, task_id: str) -> None:
        self.repository = TaskRepository(connection)
        self.task_id = task_id

    def __call__(self, prompt: str) -> str:
        matched = re.search(r'"available_evidence_ids":\s*(\[[^\]]+\])', prompt)
        assert matched is not None
        evidence_ids = json.loads(matched.group(1))
        reference = self.repository.get_evidence(self.task_id, evidence_ids[0])
        assert reference is not None
        return json.dumps(
            {
                "summary": "The report is bounded by registered evidence and has limitations.",
                "sections": [
                    {"title": "Facts", "claim_ids": ["claim-market"], "content": "QQQ has registered market data."},
                    {"title": "Counter-Evidence And Unknowns", "claim_ids": [], "content": "Limitations: no causal conclusion is established."},
                ],
                "claims": [
                    {
                        "claim_id": "claim-market",
                        "text": "QQQ has registered market data.",
                        "claim_type": "fact",
                        "confidence": 0.7,
                        "evidence_refs": [
                            {
                                "evidence_id": evidence_ids[0],
                                **reference.model_dump(mode="json", exclude={"evidence_id"}),
                            }
                        ],
                    }
                ],
                "limitations": ["The report is not a trading instruction."],
            }
        )


class InvalidThenValidReportModel(ReportModel):
    def __init__(self, connection, task_id: str) -> None:
        super().__init__(connection, task_id)
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return "not valid json"
        return super().__call__(prompt)

def test_production_v2_runs_fixture_evidence_to_final_report_and_survives_restart(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=ReportModel(connection, "task-production"),
    )
    task = components.service.submit(_request(), task_id="task-production", now=NOW)

    # Restarting the composition root keeps all intermediate step outputs durable.
    restarted = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=ReportModel(connection, "task-production"),
    )
    tick = ResearchTaskWorkerV2(restarted.service, worker_id="fixture-worker").run_once(now=NOW)

    report = restarted.service.connection.execute(
        "SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task.task_id,)
    ).fetchone()[0]
    assert report == 1
    assert restarted.service.get(task.task_id)["task"]["status"] == "completed"
    assert tick.executed_steps == 4
    assert tick.errors == []
    traces = AgentTraceRecorder(connection).list_task(task.task_id)
    assert any(trace.component == "tool" and trace.output_ref.get("provider_freshness") for trace in traces)
    assert any(trace.component == "model" and trace.output_ref.get("estimated_cost_usd") == 0.0 for trace in traces)
    connection.close()


def test_production_v2_returns_durable_evidence_gap_when_model_is_missing(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=None,
    )
    task = components.service.submit(_request(), task_id="task-no-model", now=NOW)
    for index in range(4):
        components.service.run_ready(task.task_id, worker_id=f"no-model-{index}", limit=1, now=NOW)

    artifact_id = components.service.repository.get_step_output_artifact_id(task.task_id, "step-report")
    assert artifact_id is not None
    artifact = components.service.repository.get_artifact(task.task_id, artifact_id)
    assert artifact is not None
    gap = EvidenceGapRequest.model_validate(components.service.runtime.artifact_service.load_json(task.task_id, artifact.ref))
    assert gap.requester == "report"
    status = components.service.get(task.task_id)
    assert status["task"]["status"] == "running"
    assert {item["step_id"] for item in status["evidence_gaps"]} == {"step-report", "step-validator"}
    assert any(item["reason"] == gap.reason for item in status["evidence_gaps"])
    connection.close()


def test_report_agent_uses_its_own_bounded_repair_budget(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    model = InvalidThenValidReportModel(connection, "task-report-repair")
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=model,
    )
    task = components.service.submit(_request(), task_id="task-report-repair", now=NOW)

    tick = ResearchTaskWorkerV2(components.service, worker_id="report-repair-worker").run_task(task.task_id, now=NOW)

    assert tick.errors == []
    assert model.calls == 2  # The report's invalid first draft and its own retry.
    assert components.service.get(task.task_id)["task"]["status"] == "completed"
    connection.close()


def test_production_v2_runs_two_tasks_with_reused_step_names_without_trace_collisions(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=None,
    )
    first = components.service.submit(_request(), task_id="task-reused-steps-first", now=NOW)
    second = components.service.submit(_request(), task_id="task-reused-steps-second", now=NOW)

    tick = ResearchTaskWorkerV2(components.service, worker_id="reused-step-worker").run_once(now=NOW)

    assert tick.errors == []
    assert tick.executed_steps == 8
    for task in (first, second):
        status = components.service.get(task.task_id)
        assert status["task"]["status"] == "running"
        assert components.service.repository.get_step_output_artifact_id(task.task_id, "step-data") is not None
        trace_ids = {trace.trace_id for trace in AgentTraceRecorder(connection).list_task(task.task_id)}
        assert any(task.task_id in trace_id for trace_id in trace_ids)
    connection.close()


def test_research_worker_can_drain_one_named_task_without_touching_other_running_tasks(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=None,
    )
    first = components.service.submit(_request(), task_id="task-targeted-first", now=NOW)
    second = components.service.submit(_request(), task_id="task-targeted-second", now=NOW)

    tick = ResearchTaskWorkerV2(components.service, worker_id="targeted-worker").run_task(first.task_id, now=NOW)

    assert tick.task_ids == [first.task_id]
    assert tick.errors == []
    assert components.service.repository.get_step_output_artifact_id(first.task_id, "step-data") is not None
    assert components.service.repository.get_step_output_artifact_id(second.task_id, "step-data") is None
    connection.close()


def test_current_data_request_uses_a_shorter_evidence_freshness_window(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=None,
    )
    request = _request().model_copy(update={"constraints": ResearchConstraints(require_current_data=True)})
    task = components.service.submit(request, task_id="task-current-data", now=NOW)

    ResearchTaskWorkerV2(components.service, worker_id="current-data-worker").run_task(task.task_id, now=NOW)

    artifact_id = components.service.repository.get_step_output_artifact_id(task.task_id, "step-data")
    assert artifact_id is not None
    artifact = components.service.repository.get_artifact(task.task_id, artifact_id)
    assert artifact is not None
    output = components.service.runtime.artifact_service.load_json(task.task_id, artifact.ref)
    assert output["request"]["freshness_seconds"] == 15 * 60
    connection.close()


def test_historical_request_uses_non_expiring_evidence_references(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=None,
    )
    task = components.service.submit(_request(), task_id="task-historical-data", now=NOW)

    ResearchTaskWorkerV2(components.service, worker_id="historical-data-worker").run_task(task.task_id, now=NOW)

    artifact_id = components.service.repository.get_step_output_artifact_id(task.task_id, "step-data")
    assert artifact_id is not None
    artifact = components.service.repository.get_artifact(task.task_id, artifact_id)
    assert artifact is not None
    output = components.service.runtime.artifact_service.load_json(task.task_id, artifact.ref)
    assert output["request"]["freshness_seconds"] == 0
    assert all(reference["valid_until"] is None for reference in output["evidence_refs"])
    connection.close()


def test_production_handler_dispatches_specialist_retry_steps() -> None:
    handler = object.__new__(ProductionStepHandlerV2)
    context = SimpleNamespace(step=SimpleNamespace(step_id=""))
    handler._discover_signal = lambda _context: "signal"  # type: ignore[method-assign]
    handler._analyze_anomaly = lambda _context: "anomaly"  # type: ignore[method-assign]
    handler._analyze_macro = lambda _context, _input: "macro"  # type: ignore[method-assign]

    context.step.step_id = "step-signal_discovery-retry-r2"
    assert handler.run(context, None) == "signal"
    context.step.step_id = "step-anomaly_analysis-retry-r2"
    assert handler.run(context, None) == "anomaly"
    context.step.step_id = "step-macro_analysis-retry-r2"
    assert handler.run(context, {"source": "test"}) == "macro"


def test_research_worker_replans_only_a_retryable_provider_gap(tmp_path: Path) -> None:
    FailOnceProvider.calls = 0
    connection = initialize_database(tmp_path / "runtime.sqlite")
    data_workflow, news_workflow = _workflows(tmp_path, connection, provider_factory=FailOnceProvider)
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=ReportModel(connection, "task-retry"),
    )
    task = components.service.submit(_request(), task_id="task-retry", now=NOW)

    tick = ResearchTaskWorkerV2(components.service, worker_id="retry-worker").run_once(now=NOW)

    assert tick.replans == 1
    assert components.service.get(task.task_id)["task"]["status"] == "completed"
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task.task_id,)).fetchone()[0] == 1
    connection.close()


def _workflows(root: Path, connection, *, provider_factory=FixtureProvider):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = "fixture"
    config["provider"]["priority"] = ["fixture"]
    config["provider"]["fallback"] = {"enabled": False, "order": []}
    config["news"]["cache_ttl_minutes"] = 60
    validated = validate_config(config)
    registry = ProviderRegistry(
        root=root,
        config=validated,
        connection=connection,
        provider_factories={"fixture": provider_factory},
        allowed_provider_names=V2_READ_ONLY_PROVIDER_NAMES | {"fixture"},
    )
    data = DataEvidenceWorkflow(root=root, connection=connection, provider_registry=registry)
    news = NewsEvidenceWorkflow(
        root=root,
        connection=connection,
        query_service=NewsQueryService(
            connection,
            config=validated.news,
            provider=StaticNewsProvider(
                [
                    NewsItem(
                        news_id="news-fixture",
                        symbol="QQQ",
                        market="US",
                        title="QQQ fixture update",
                        summary="Fixture news is evidence, not an instruction.",
                        url="https://example.test/qqq",
                        source="fixture-news",
                        published_at=datetime(2026, 5, 22, 15, 0, tzinfo=UTC),
                        created_at=NOW,
                    )
                ]
            ),
            allow_local_provider_without_api_key=True,
        ),
    )
    return data, news


def _request() -> ResearchRequest:
    return ResearchRequest(
        request_id="request-production-v2",
        question="Create a bounded QQQ facts report.",
        symbols=["QQQ"],
        time_window=WINDOW,
        report_type="facts",
    )
