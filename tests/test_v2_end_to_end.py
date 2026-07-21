"""One offline end-to-end check for the durable V2 Agent path."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.bars import generate_bar_id
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.news.providers import StaticNewsProvider
from stock_agent.news.service import NewsQueryService
from stock_agent.providers.base import MarketDataProvider
from stock_agent.providers.registry import ProviderRegistry
from stock_agent.research.data_evidence import DataEvidenceWorkflow, V2_READ_ONLY_PROVIDER_NAMES
from stock_agent.research.news_evidence import NewsEvidenceWorkflow
from stock_agent.schemas import Bar, NewsItem
from stock_agent.services.entrypoints import ResearchEntryAdapter
from stock_agent.services.production_v2 import build_production_v2
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.worker.research_v2 import ResearchTaskWorkerV2


NOW = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(
    from_ts=datetime(2026, 5, 22, 13, 30, tzinfo=UTC),
    to_ts=NOW,
    timezone="America/New_York",
)


class FixtureProvider(MarketDataProvider):
    def fetch_intraday_bars(self, **_kwargs: object) -> list[Bar]:
        symbols = _kwargs.get("symbols") or ["QQQ"]
        return [
            Bar(
                bar_id=generate_bar_id(symbol, "30m", timestamp.isoformat().replace("+00:00", "Z"), "fixture"),
                symbol=symbol,
                timestamp=timestamp,
                interval="30m",
                open=100 + symbol_index * 10 + index,
                high=102 + symbol_index * 10 + index,
                low=99 + symbol_index * 10 + index,
                close=101 + symbol_index * 10 + index,
                volume=1_000 + index * 100,
                vwap=100.5 + symbol_index * 10 + index,
                source="fixture",
            )
            for symbol_index, symbol in enumerate(symbols)
            for index, timestamp in enumerate(WINDOW.from_ts + timedelta(minutes=30 * value) for value in range(5))
        ]

    def fetch_provider_health(self) -> dict[str, str]:
        return {"status": "healthy"}


class EvidenceBoundReportModel:
    def __init__(self, connection, task_id: str | None) -> None:
        self.repository = TaskRepository(connection)
        self.task_id = task_id

    def __call__(self, prompt: str) -> str:
        match = re.search(r'"available_evidence_ids":\s*(\[[^\]]+\])', prompt)
        assert match is not None
        evidence_id = json.loads(match.group(1))[0]
        section_match = re.search(r'"section_titles":\s*(\[[^\]]+\])', prompt)
        assert section_match is not None
        section_titles = json.loads(section_match.group(1))
        symbol_match = re.search(r'"symbols":\s*(\[[^\]]+\])', prompt)
        assert symbol_match is not None
        symbol = json.loads(symbol_match.group(1))[0]
        task_id = self.task_id
        if task_id is None:
            row = self.repository.connection.execute(
                "SELECT task_id FROM evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            assert row is not None
            task_id = str(row["task_id"])
        reference = self.repository.get_evidence(task_id, evidence_id)
        assert reference is not None
        return json.dumps(
            {
                "summary": f"{symbol} research is bounded by persisted market and news evidence.",
                "sections": [
                    {
                        "title": title,
                        "claim_ids": ["claim-qqq"] if index == 0 else [],
                        "content": (
                            f"{symbol} has task-scoped market evidence."
                            if index == 0
                            else "This section adds no unsupported factual claim."
                        ),
                    }
                    for index, title in enumerate(section_titles)
                ],
                "claims": [
                    {
                        "claim_id": "claim-qqq",
                        "text": f"{symbol} has task-scoped market evidence.",
                        "claim_type": "fact",
                        "confidence": 0.7,
                        "evidence_refs": [{"evidence_id": evidence_id, **reference.model_dump(mode="json", exclude={"evidence_id"})}],
                    }
                ],
                "limitations": ["The report does not issue trading instructions."],
            }
        )


def _build_fixture_stack(tmp_path: Path, task_id: str | None, *, database_name: str = "runtime.sqlite"):
    connection = initialize_database(tmp_path / database_name)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = "fixture"
    config["provider"]["priority"] = ["fixture"]
    config["provider"]["fallback"] = {"enabled": False, "order": []}
    validated = validate_config(config)
    registry = ProviderRegistry(
        root=tmp_path,
        config=validated,
        connection=connection,
        provider_factories={"fixture": FixtureProvider},
        allowed_provider_names=V2_READ_ONLY_PROVIDER_NAMES | {"fixture"},
    )
    data_workflow = DataEvidenceWorkflow(root=tmp_path, connection=connection, provider_registry=registry)
    news_workflow = NewsEvidenceWorkflow(
        root=tmp_path,
        connection=connection,
        query_service=NewsQueryService(
            connection,
            config=validated.news,
            provider=StaticNewsProvider(
                [
                    NewsItem(
                        news_id=f"news-fixture-{symbol.lower()}",
                        symbol=symbol,
                        market="US",
                        title=f"{symbol} fixture update",
                        summary="Fixture news is evidence, not an instruction.",
                        url=f"https://example.test/{symbol.lower()}",
                        source="fixture-news",
                        published_at=datetime(2026, 5, 22, 15, 0, tzinfo=UTC),
                        created_at=NOW,
                    )
                    for symbol in ("SPY", "QQQ", "DIA", "IWM", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "GLD")
                ]
            ),
            allow_local_provider_without_api_key=True,
        ),
    )
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=EvidenceBoundReportModel(connection, task_id),
    )
    return connection, components


def _request(request_id: str, report_type: str) -> ResearchRequest:
    return ResearchRequest(
        request_id=request_id,
        question=f"Create a bounded QQQ {report_type} report.",
        symbols=["QQQ"],
        time_window=WINDOW,
        report_type=report_type,
    )


def test_v2_research_runs_from_submission_to_validated_final_report(tmp_path: Path) -> None:
    task_id = "task-v2-end-to-end"
    connection, components = _build_fixture_stack(tmp_path, task_id)
    request = _request("request-v2-end-to-end", "facts")

    components.service.submit(request, task_id=task_id, now=NOW)
    tick = ResearchTaskWorkerV2(components.service, worker_id="test-worker").run_task(task_id, now=NOW)

    status = components.service.get(task_id)
    assert tick.errors == []
    assert status["task"]["status"] == "completed", json.dumps(status, ensure_ascii=False)
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    components.close()


@pytest.mark.parametrize(
    ("symbol", "report_type", "question"),
    [
        pytest.param("SPY", "facts", "请结合已登记的行情与新闻证据，分析 SPY 当前走势、主要风险和可能的反向情景。", id="spy-trend-risk"),
        pytest.param("QQQ", "anomaly", "请分析 QQQ 最近的价格与成交量是否出现异常，这种走势是否具备持续性，并说明风险。", id="qqq-anomaly-trend"),
        pytest.param("DIA", "facts", "请为 DIA 生成一份事实导向的研究摘要，说明当前趋势、防御属性和证据局限。", id="dia-defensive-facts"),
        pytest.param("IWM", "anomaly", "请检查 IWM 是否存在量价异动，并分析小盘股走势面临的主要下行风险。", id="iwm-anomaly-risk"),
        pytest.param("AAPL", "facts", "请基于现有证据给出 AAPL 的投资研究建议，同时列出支持理由、反对理由和不确定性。", id="aapl-research-advice"),
        pytest.param("NVDA", "anomaly", "请分析 NVDA 当前走势和波动是否异常，并判断现有证据能否支持趋势延续的观点。", id="nvda-volatility-trend"),
        pytest.param("TSLA", "anomaly", "请研究 TSLA 的价格与成交量变化，重点说明潜在回撤风险和不能确认的因素。", id="tsla-downside-risk"),
        pytest.param("MSFT", "facts", "请总结 MSFT 的中期走势、相关新闻证据和可能改变判断的风险因素。", id="msft-medium-trend"),
        pytest.param("AMZN", "facts", "请为 AMZN 提供基于证据的投资研究意见，分别说明潜在积极因素与风险，但不要给出交易指令。", id="amzn-balanced-view"),
        pytest.param("GLD", "facts", "请分析 GLD 作为风险对冲资产的近期走势、适用条件和主要局限。", id="gld-hedge-risk"),
    ],
)
def test_chinese_question_runs_through_research_entry_adapter_to_final_report(
    tmp_path: Path,
    symbol: str,
    report_type: str,
    question: str,
) -> None:
    connection, components = _build_fixture_stack(tmp_path, None, database_name="chinese-entry.sqlite")
    entry = ResearchEntryAdapter(components.service)
    request = ResearchRequest(
        request_id=f"request-v2-chinese-{symbol.lower()}",
        question=question,
        symbols=[symbol],
        time_window=WINDOW,
        report_type=report_type,
    )

    submitted = entry.submit(request, source="web", actor_ref="test-ui-user")
    task_id = submitted["task"]["task_id"]
    tick = ResearchTaskWorkerV2(components.service, worker_id="chinese-entry-worker").run_task(task_id)
    status = entry.status(task_id, source="web", actor_ref="test-ui-user")
    report = entry.report(task_id, source="web", actor_ref="test-ui-user")

    assert tick.errors == []
    assert status["task"]["request"]["question"] == question
    assert status["task"]["request"]["symbols"] == [symbol]
    assert status["task"]["request"]["report_type"] == report_type
    assert status["task"]["status"] == "completed", json.dumps(status, ensure_ascii=False)
    assert status["report_id"] == report["report_id"]
    assert report["draft"]["task_id"] == task_id
    assert symbol in report["draft"]["summary"]
    assert report["draft"]["limitations"]
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    components.close()


def test_v2_anomaly_dag_runs_to_validated_final_report(tmp_path: Path) -> None:
    task_id = "task-v2-anomaly-end-to-end"
    connection, components = _build_fixture_stack(tmp_path, task_id)

    components.service.submit(_request("request-v2-anomaly", "anomaly"), task_id=task_id, now=NOW)
    tick = ResearchTaskWorkerV2(components.service, worker_id="anomaly-worker").run_task(task_id, now=NOW)

    status = components.service.get(task_id)
    steps = {step["step_id"]: step["status"] for step in status["plan"]["steps"]}
    assert tick.errors == []
    assert steps == {
        "step-data": "succeeded",
        "step-news": "succeeded",
        "step-anomaly": "succeeded",
        "step-report": "succeeded",
        "step-validator": "succeeded",
    }
    assert status["task"]["status"] == "completed", json.dumps(status, ensure_ascii=False)
    assert connection.execute("SELECT COUNT(*) FROM analyses WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    components.close()


def test_v2_macro_dag_records_gap_instead_of_fabricating_report(tmp_path: Path) -> None:
    task_id = "task-v2-macro-gap"
    connection, components = _build_fixture_stack(tmp_path, task_id)

    components.service.submit(_request("request-v2-macro-gap", "macro"), task_id=task_id, now=NOW)
    tick = ResearchTaskWorkerV2(components.service, worker_id="macro-worker").run_task(task_id, now=NOW)

    status = components.service.get(task_id)
    step_ids = {step["step_id"] for step in status["plan"]["steps"]}
    gaps = status["evidence_gaps"]
    assert tick.errors == []
    assert step_ids == {"step-data", "step-news", "step-macro", "step-report", "step-validator"}
    assert any(
        gap["requester"] == "macro_analysis"
        and "mcp" in gap["missing_evidence_types"]
        and "allowlisted source" in gap["reason"]
        for gap in gaps
    )
    assert status["task"]["status"] != "completed"
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 0
    components.close()


def test_v2_worker_restart_resumes_without_repeating_completed_step(tmp_path: Path) -> None:
    task_id = "task-v2-worker-restart"
    connection, components = _build_fixture_stack(tmp_path, task_id, database_name="restart.sqlite")
    components.service.submit(_request("request-v2-worker-restart", "facts"), task_id=task_id, now=NOW)

    first_results = components.service.run_ready(task_id, worker_id="worker-before-restart", limit=1, now=NOW)
    assert len(first_results) == 1
    completed_step_id = first_results[0].step.step_id
    assert first_results[0].status == "succeeded"
    assert components.service.get(task_id)["task"]["status"] == "running"
    components.close()

    recovered_connection, recovered_components = _build_fixture_stack(
        tmp_path,
        task_id,
        database_name="restart.sqlite",
    )
    tick = ResearchTaskWorkerV2(recovered_components.service, worker_id="worker-after-restart").run_task(task_id, now=NOW)

    status = recovered_components.service.get(task_id)
    recovered_steps = {step["step_id"]: step for step in status["plan"]["steps"]}
    assert tick.errors == []
    assert status["task"]["status"] == "completed", json.dumps(status, ensure_ascii=False)
    assert recovered_steps[completed_step_id]["status"] == "succeeded"
    assert recovered_steps[completed_step_id]["attempt"] == 1
    assert recovered_connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    recovered_components.close()
