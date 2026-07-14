from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_agent.contracts.analysis import AnalysisMetric, AnomalyAnalysis
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import ArtifactRef, EvidenceRef
from stock_agent.contracts.reports import (
    ClaimValidationResult,
    FinalReport,
    ReportClaim,
    ReportDraft,
    ReportSection,
    ReportValidationResult,
)
from stock_agent.contracts.signals import (
    CandidateFunction,
    ExistingSignal,
    LeakageCheck,
    SignalApproval,
    SignalObservation,
    SignalValidationResult,
    SignalVersion,
    StabilityResult,
    ValidationSplitResult,
)
from stock_agent.contracts.tasks import AgentMessage, AgentPlan, AgentStep, AgentTask, ResearchRequest
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import RepositoryStateError, TaskRepository


NOW = datetime(2026, 7, 1, 15, 30, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=datetime(2026, 6, 1, tzinfo=UTC), to_ts=NOW, timezone="America/New_York")
HASH = "a" * 64


def test_task_repository_persists_task_plan_messages_evidence_and_claims_once(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite"
    connection = initialize_database(database_path)
    repository = TaskRepository(connection)
    task = _task("task-1")
    repository.create_task(task)
    artifact = _artifact("bars-1", "bars")
    evidence = _evidence("evidence-1", artifact.artifact_id)
    repository.register_artifact(task.task_id, artifact, storage_key="artifacts/bars-1.json")
    repository.register_evidence(task.task_id, evidence)
    plan = AgentPlan(
        plan_id="plan-1",
        task_id=task.task_id,
        reason="collect facts then write a report",
        steps=[
            AgentStep(step_id="step-data", actor="orchestrator"),
            AgentStep(step_id="step-report", actor="report", depends_on=["step-data"]),
        ],
    )
    repository.save_plan(plan, created_at=NOW)
    repository.add_message(
        AgentMessage(
            message_id="message-1",
            task_id=task.task_id,
            sender="orchestrator",
            recipient="report",
            summary="Use the attached evidence references only.",
            artifact_refs=[artifact],
            evidence_refs=[evidence],
            created_at=NOW,
        )
    )

    second_connection = initialize_database(database_path)
    second_repository = TaskRepository(second_connection)
    claimed = repository.claim_next_step(task.task_id, worker_id="worker-a", claimed_at=NOW)
    blocked_by_dependency = second_repository.claim_next_step(task.task_id, worker_id="worker-b", claimed_at=NOW)
    completed = repository.complete_step(
        "step-data", expected_status="running", new_status="succeeded", updated_at=NOW
    )
    report_step = repository.claim_next_step(task.task_id, worker_id="worker-b", claimed_at=NOW)

    assert repository.get_task(task.task_id) == task
    persisted_plan = repository.get_plan(plan.plan_id)
    assert persisted_plan is not None and persisted_plan.plan_id == plan.plan_id
    assert [step.status for step in persisted_plan.steps] == ["succeeded", "running"]
    assert repository.list_messages(task.task_id)[0].evidence_refs == [evidence]
    assert claimed is not None and claimed.step_id == "step-data" and claimed.attempt == 1
    assert blocked_by_dependency is None
    assert completed.status == "succeeded"
    assert report_step is not None and report_step.step_id == "step-report"
    second_connection.close()
    connection.close()


def test_task_repository_rejects_invalid_state_and_cross_task_evidence(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    first = _task("task-first")
    second = _task("task-second")
    repository.create_task(first)
    repository.create_task(second)
    artifact = _artifact("bars-cross", "bars")
    evidence = _evidence("evidence-cross", artifact.artifact_id)
    repository.register_artifact(first.task_id, artifact, storage_key="artifacts/bars-cross.json")

    with pytest.raises(RepositoryStateError):
        repository.transition_task(first.task_id, expected_status="pending", new_status="completed", updated_at=NOW)
    with pytest.raises(RepositoryStateError):
        repository.register_evidence(second.task_id, evidence)

    running = repository.transition_task(first.task_id, expected_status="pending", new_status="running", updated_at=NOW)
    assert running.status == "running"
    connection.close()


def test_signal_and_report_repositories_round_trip_with_foreign_keys(tmp_path: Path) -> None:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    task_repository = TaskRepository(connection)
    task_repository.create_task(_task("task-signal"))
    source_artifact = _artifact("candidate-source", "candidate_source")
    bars_artifact = _artifact("bars-validation", "bars")
    evidence = _evidence("evidence-signal", bars_artifact.artifact_id)
    task_repository.register_artifact("task-signal", source_artifact, storage_key="artifacts/candidate.py")
    task_repository.register_artifact("task-signal", bars_artifact, storage_key="artifacts/bars.json")
    task_repository.register_evidence("task-signal", evidence)
    signal_repository = SignalRepository(connection)
    signal_repository.upsert_definition(
        ExistingSignal(
            signal_id="signal-v2",
            version=1,
            name="volume confirmation",
            feature_fingerprint="volume-return-v1",
            status="validated",
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    candidate = CandidateFunction(
        candidate_id="candidate-1",
        proposal_id="proposal-1",
        interface_version="v1",
        source_artifact=source_artifact,
        source_hash=HASH,
    )
    validation = SignalValidationResult(
        validation_id="validation-1",
        candidate_id=candidate.candidate_id,
        dataset_refs=[bars_artifact],
        split_results=[
            ValidationSplitResult(
                split_name="holdout",
                time_window=WINDOW,
                sample_count=20,
                observation_count=5,
                deterministic=True,
                error_rate=0.1,
            )
        ],
        leakage_checks=[LeakageCheck(name="future-data", passed=True, details="no future bars")],
        stability=StabilityResult(passed=True, coverage=0.8),
        decision="pass",
    )
    version = SignalVersion(
        signal_id="signal-v2",
        version=1,
        status="validated",
        source_hash=HASH,
        validation_id=validation.validation_id,
    )
    observation = SignalObservation(
        signal_id="signal-v2",
        version=1,
        symbol="QQQ",
        timestamp=NOW,
        label="positive",
        strength=0.7,
        confidence=0.8,
        reason="confirmed by volume",
        evidence_refs=[evidence],
    )
    signal_repository.save_candidate(candidate, created_at=NOW)
    signal_repository.save_validation(validation, created_at=NOW)
    signal_repository.save_version(version)
    signal_repository.append_observation(observation)
    signal_repository.record_approval(
        SignalApproval(
            approval_id="approval-1",
            signal_id="signal-v2",
            version=1,
            decision="approved",
            decided_by="admin-1",
            decided_at=NOW,
        )
    )
    report_repository = ReportRepository(connection)
    analysis = AnomalyAnalysis(
        analysis_id="analysis-1",
        metrics=[AnalysisMetric(name="return", value=0.03, evidence_refs=[evidence])],
        baseline="20-day mean",
        confidence=0.6,
        evidence_refs=[evidence],
        created_at=NOW,
    )
    draft = ReportDraft(
        draft_id="draft-1",
        task_id="task-signal",
        summary="The move is supported by a confirmed volume change.",
        sections=[ReportSection(title="Evidence", claim_ids=["claim-1"], content="Volume was elevated.")],
        claims=[
            ReportClaim(
                claim_id="claim-1",
                text="Volume was elevated relative to the selected baseline.",
                claim_type="fact",
                confidence=0.8,
                evidence_refs=[evidence],
            )
        ],
        generated_at=NOW,
    )
    final = FinalReport(
        report_id="report-1",
        draft=draft,
        validation=ReportValidationResult(
            status="passed",
            claim_results=[ClaimValidationResult(claim_id="claim-1", status="passed")],
        ),
        published_at=NOW,
    )
    report_repository.save_analysis("task-signal", analysis)
    report_repository.save_draft(draft)
    report_repository.save_final(final)

    assert signal_repository.get_definition("signal-v2") is not None
    assert signal_repository.get_candidate(candidate.candidate_id) == candidate
    assert signal_repository.get_validation(validation.validation_id) == validation
    assert signal_repository.get_version("signal-v2", 1) == version
    assert signal_repository.list_observations("signal-v2", 1) == [observation]
    assert report_repository.get_analysis(analysis.analysis_id) == analysis
    assert report_repository.get_draft(draft.draft_id) == draft
    assert report_repository.get_final(final.report_id) == final
    connection.close()


def _task(task_id: str) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        request=ResearchRequest(
            request_id=f"request-{task_id}",
            question="Generate a research report from the selected evidence.",
            symbols=["QQQ"],
            time_window=WINDOW,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _artifact(artifact_id: str, kind: str) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        kind=kind,
        sha256=HASH if kind == "candidate_source" else "b" * 64,
        media_type="application/json",
        size_bytes=128,
        created_at=NOW,
    )


def _evidence(evidence_id: str, artifact_id: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        evidence_type="bar",
        artifact_id=artifact_id,
        source="csv_demo",
        observed_at=NOW,
        trust_level="high",
    )
