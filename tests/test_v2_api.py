from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.evidence import EvidenceRef
from stock_agent.contracts.reports import (
    ClaimValidationResult,
    FinalReport,
    ReportClaim,
    ReportDraft,
    ReportSection,
    ReportValidationResult,
)
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.services.agent_service import AgentService
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.web import create_app


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class Output(StrictSchema):
    value: str


class NoInputHandler:
    def run(self, context, _typed_input) -> Output:
        return Output(value=context.step.actor)


def test_v2_api_submits_controls_streams_and_renders_one_task(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    app = create_app(tmp_path, v2_agent_service=service)
    with TestClient(app) as client:
        submitted = client.post("/api/v2/research", json={"request": _request().model_dump(mode="json")})
        assert submitted.status_code == 200
        task_id = submitted.json()["task"]["task_id"]

        status = client.get(f"/api/v2/research/{task_id}")
        assert status.status_code == 200
        assert status.json()["task"]["task_id"] == task_id
        assert status.json()["task"]["status"] == "running"
        assert status.json()["report_id"] is None

        diagnostics = client.get(f"/api/v2/research/{task_id}/diagnostics")
        assert diagnostics.status_code == 200
        assert diagnostics.json()["budget"]["used_model_calls"] == 0
        assert diagnostics.json()["traces"]

        paused = client.post(f"/api/v2/research/{task_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["task"]["status"] == "paused"

        resumed = client.post(f"/api/v2/research/{task_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["task"]["status"] == "running"

        report_retry = client.post(f"/api/v2/research/{task_id}/retry-report")
        assert report_retry.status_code == 409
        assert "completed initial report step" in report_retry.json()["detail"]

        event = client.get(f"/api/v2/research/{task_id}/events?once=true")
        assert event.status_code == 200
        assert "event: research_status" in event.text
        assert task_id in event.text

        report_id = _save_final_report(connection, task_id)
        json_report = client.get(f"/api/v2/research/{task_id}/reports/{report_id}")
        markdown_report = client.get(f"/api/v2/research/{task_id}/reports/{report_id}?format=markdown")
        assert json_report.status_code == 200
        assert json_report.json()["report_id"] == report_id
        assert markdown_report.status_code == 200
        assert markdown_report.headers["content-type"].startswith("text/markdown")
        assert "# Research Report" in markdown_report.text

        cancelled = client.post(f"/api/v2/research/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["task"]["status"] == "cancelled"
    connection.close()


def test_v2_api_maps_safety_and_missing_task_errors(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    app = create_app(tmp_path, v2_agent_service=service)
    prohibited = _request().model_copy(update={"question": "buy 10 shares of QQQ now"})
    with TestClient(app) as client:
        blocked = client.post("/api/v2/research", json={"request": prohibited.model_dump(mode="json")})
        missing = client.get("/api/v2/research/task-missing")
    connection.close()

    assert blocked.status_code == 403
    assert "blocked_trading_or_position" in blocked.json()["detail"]
    assert missing.status_code == 404


def _service(root: Path) -> tuple[object, AgentService]:
    connection = initialize_runtime_database(root)
    repository = TaskRepository(connection)
    registry = AgentRegistry()
    for role in ("orchestrator", "signal_discovery", "anomaly_analysis", "macro_analysis", "report"):
        registry.register(AgentRegistration(role=role, handler=NoInputHandler(), output_schema=Output))
    runtime = AgentRuntime(
        repository=repository,
        artifact_service=ArtifactService(ArtifactStore(connection, root / "lake")),
        registry=registry,
    )
    return connection, AgentService(connection, runtime=runtime)


def _request() -> ResearchRequest:
    return ResearchRequest(
        request_id="request-v2-api",
        question="Create a bounded QQQ research report.",
        symbols=["QQQ"],
        time_window=TimeWindow(
            from_ts=NOW - timedelta(days=1),
            to_ts=NOW,
            timezone="America/New_York",
        ),
    )


def _save_final_report(connection, task_id: str) -> str:
    draft = ReportDraft(
        draft_id=f"draft-{task_id}",
        task_id=task_id,
        summary="A validated, bounded research summary.",
        sections=[ReportSection(title="Facts", claim_ids=["claim-1"], content="Verified evidence is required.")],
        claims=[
            ReportClaim(
                claim_id="claim-1",
                text="Only verified evidence supports this report.",
                claim_type="fact",
                confidence=0.8,
                evidence_refs=[
                    EvidenceRef(
                        evidence_id="evidence-v2-api",
                        evidence_type="bar",
                        artifact_id="artifact-v2-api",
                        source="fixture",
                        observed_at=NOW,
                    )
                ],
            )
        ],
        generated_at=NOW,
    )
    report = FinalReport(
        report_id=f"report-{task_id}",
        draft=draft,
        validation=ReportValidationResult(
            status="passed",
            claim_results=[ClaimValidationResult(claim_id="claim-1", status="passed")],
        ),
        published_at=NOW,
    )
    repository = ReportRepository(connection)
    repository.save_draft(draft)
    repository.save_final(report)
    return report.report_id
