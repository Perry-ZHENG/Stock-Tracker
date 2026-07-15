from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.agents.report import ReportAgent
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.contracts.reports import ReportClaim, ReportDraft, ReportSection
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.reports.bundle import ReportBundleBuilder, ReportBundleRequest
from stock_agent.reports.renderers import ReportRenderError, render_report
from stock_agent.reports.service import ReportPolicy, ReportService
from stock_agent.security.research_policy import ResearchSafetyPolicy
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.validation.claims import ClaimValidator
from stock_agent.validation.evidence import EvidenceValidator
from stock_agent.validation.report import ReportValidator


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=1), to_ts=NOW, timezone="America/New_York")


def test_report_pipeline_publishes_validated_final_and_rendered_artifacts(tmp_path: Path) -> None:
    service, bundle_request, connection = _service(tmp_path)
    result = service.publish(bundle_request, now=NOW)

    assert result.status == "published"
    assert result.final_report is not None
    assert result.validation.status == "passed"
    assert {artifact.media_type for artifact in result.artifacts} == {"application/json", "text/markdown"}
    markdown = service.artifact_service.open_bytes(bundle_request.task_id, next(item for item in result.artifacts if item.media_type == "text/markdown")).decode()
    assert "evidence://evidence-report-pipeline" in markdown
    assert "No verified news evidence" in markdown
    connection.close()


def test_report_pipeline_returns_revision_or_rejection_without_final_bypass(tmp_path: Path) -> None:
    service, bundle_request, connection = _service(tmp_path)
    missing = service.publish(bundle_request.model_copy(update={"evidence_ids": ["missing"]}), now=NOW)
    conflict = service.publish(bundle_request, known_conflicts=["provider disagreement"], now=NOW)
    exhausted = service.publish(bundle_request, policy=ReportPolicy(max_revisions=0), revision=1, now=NOW)

    assert missing.status == "needs_revision"
    assert conflict.status == "needs_revision"
    assert exhausted.status == "rejected"
    assert exhausted.final_report is None
    connection.close()


def test_renderers_reject_drafts_and_service_restarts_from_persisted_state(tmp_path: Path) -> None:
    service, bundle_request, connection = _service(tmp_path)
    published = service.publish(bundle_request, now=NOW)
    draft = published.draft
    assert draft is not None
    with pytest.raises(ReportRenderError):
        render_report(draft, "markdown")  # type: ignore[arg-type]

    restarted, request_after_restart, _same_connection = _service(tmp_path, connection=connection)
    repeated = restarted.publish(request_after_restart, now=NOW)
    assert repeated.status == "published"
    assert restarted.repository.get_final(repeated.final_report.report_id) == repeated.final_report
    connection.close()


class ScriptedModel:
    def __call__(self, _prompt: str) -> str:
        return json.dumps(
            {
                "summary": "A bounded report based on verified evidence.",
                "sections": [
                    {"title": "Facts", "claim_ids": ["claim-1"], "content": "QQQ closed at 101.0 on 2027-01-02."},
                    {"title": "Counter-Evidence And Unknowns", "claim_ids": [], "content": "No additional conclusion is supported."},
                ],
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "QQQ closed at 101.0 on 2027-01-02.",
                        "claim_type": "fact",
                        "confidence": 0.8,
                        "evidence_refs": [self.reference.model_dump(mode="json")],
                    }
                ],
                "limitations": ["The report is not a trading instruction."],
            }
        )

    def with_reference(self, reference):
        self.reference = reference
        return self


def _service(tmp_path: Path, *, connection=None):
    connection = connection or initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    if repository.get_task("task-report-pipeline") is None:
        repository.create_task(
            AgentTask(
                task_id="task-report-pipeline",
                request=_request(),
                created_at=NOW,
                updated_at=NOW,
            )
        )
    artifacts = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    evidence = EvidenceService(connection, artifacts.store)
    existing = repository.get_evidence("task-report-pipeline", "evidence-report-pipeline")
    if existing is None:
        artifact = artifacts.save_json(
            "task-report-pipeline",
            kind="bars",
            payload={"bars": [{"symbol": "QQQ", "timestamp": "2027-01-02T19:30:00Z", "close": 101.0}]},
            source="fixture",
            created_at=NOW,
        )
        reference = evidence.create(
            "task-report-pipeline",
            artifact=artifact,
            evidence_type="bar",
            source="fixture",
            observed_at=NOW,
            evidence_id="evidence-report-pipeline",
        )
    else:
        reference = existing
    report_repository = ReportRepository(connection)
    validator = ReportValidator(ClaimValidator(EvidenceValidator(evidence), ResearchSafetyPolicy(connection)))
    agent = ReportAgent(model_client=ScriptedModel().with_reference(reference), artifact_service=artifacts)
    service = ReportService(
        bundle_builder=ReportBundleBuilder(evidence_service=evidence, report_repository=report_repository),
        report_agent=agent,
        validator=validator,
        repository=report_repository,
        artifact_service=artifacts,
    )
    return service, ReportBundleRequest(task_id="task-report-pipeline", request=_request(), evidence_ids=[reference.evidence_id]), connection


def _request() -> ResearchRequest:
    return ResearchRequest(
        request_id="request-report-pipeline",
        question="Summarize the verified QQQ evidence.",
        symbols=["QQQ"],
        time_window=WINDOW,
        report_type="facts",
    )
