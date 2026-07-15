"""One offline end-to-end check for the durable V2 Agent path."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
        return [
            Bar(
                bar_id=generate_bar_id("QQQ", "30m", timestamp.isoformat().replace("+00:00", "Z"), "fixture"),
                symbol="QQQ",
                timestamp=timestamp,
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
            for timestamp in [WINDOW.from_ts + timedelta(minutes=30 * index)]
        ]

    def fetch_provider_health(self) -> dict[str, str]:
        return {"status": "healthy"}


class EvidenceBoundReportModel:
    def __init__(self, connection, task_id: str) -> None:
        self.repository = TaskRepository(connection)
        self.task_id = task_id

    def __call__(self, prompt: str) -> str:
        match = re.search(r'"available_evidence_ids":\s*(\[[^\]]+\])', prompt)
        assert match is not None
        evidence_id = json.loads(match.group(1))[0]
        reference = self.repository.get_evidence(self.task_id, evidence_id)
        assert reference is not None
        return json.dumps(
            {
                "summary": "QQQ research is bounded by persisted market and news evidence.",
                "sections": [
                    {"title": "Facts", "claim_ids": ["claim-qqq"], "content": "QQQ has task-scoped market evidence."},
                    {"title": "Counter-Evidence And Unknowns", "claim_ids": [], "content": "This is research, not a trading instruction."},
                ],
                "claims": [
                    {
                        "claim_id": "claim-qqq",
                        "text": "QQQ has task-scoped market evidence.",
                        "claim_type": "fact",
                        "confidence": 0.7,
                        "evidence_refs": [{"evidence_id": evidence_id, **reference.model_dump(mode="json", exclude={"evidence_id"})}],
                    }
                ],
                "limitations": ["The report does not issue trading instructions."],
            }
        )


def test_v2_research_runs_from_submission_to_validated_final_report(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
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
    task_id = "task-v2-end-to-end"
    components = build_production_v2(
        tmp_path,
        connection=connection,
        data_workflow=data_workflow,
        news_workflow=news_workflow,
        model_client=EvidenceBoundReportModel(connection, task_id),
    )
    request = ResearchRequest(
        request_id="request-v2-end-to-end",
        question="Create a bounded QQQ facts report.",
        symbols=["QQQ"],
        time_window=WINDOW,
        report_type="facts",
    )

    components.service.submit(request, task_id=task_id, now=NOW)
    tick = ResearchTaskWorkerV2(components.service, worker_id="test-worker").run_task(task_id, now=NOW)

    status = components.service.get(task_id)
    assert tick.errors == []
    assert status["task"]["status"] == "completed", json.dumps(status, ensure_ascii=False)
    assert connection.execute("SELECT COUNT(*) FROM final_reports WHERE task_id = ?", (task_id,)).fetchone()[0] == 1
    components.close()
