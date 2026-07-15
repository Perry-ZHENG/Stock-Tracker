"""Contract tests for the V2 research-domain boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from stock_agent.contracts import (
    AgentMessage,
    AgentPlan,
    AgentStep,
    AgentTask,
    AnalysisMetric,
    AnomalyAnalysis,
    ArtifactRef,
    CandidateCause,
    CandidateFunction,
    ClaimValidationResult,
    DataEvidence,
    DataEvidenceRequest,
    DataFeature,
    DataQuality,
    EvidenceBundle,
    EvidenceRef,
    ExecutionBudget,
    ExistingSignal,
    FinalReport,
    LeakageCheck,
    MacroAnalysis,
    MacroEvent,
    MacroScenario,
    NewsCluster,
    NewsCoverage,
    NewsEvidence,
    NewsEvidenceRequest,
    ProviderReference,
    ReportClaim,
    ReportDraft,
    ReportSection,
    ReportValidationResult,
    ResearchConstraints,
    ResearchRequest,
    SignalDiscoveryConstraints,
    SignalDiscoveryInput,
    SignalApproval,
    SignalFeature,
    SignalObservation,
    SignalProposal,
    SignalValidationFeedback,
    SignalValidationResult,
    SignalVersion,
    StabilityResult,
    TimeWindow,
    ToolError,
    ToolRequest,
    ToolResult,
    TransmissionPath,
    ValidationSplitResult,
)
from stock_agent.schemas import Signal

NOW = datetime(2026, 7, 14, 14, 0, tzinfo=UTC)
HASH = "a" * 64


def window() -> TimeWindow:
    return TimeWindow(
        from_ts=NOW - timedelta(days=5),
        to_ts=NOW,
        timezone="America/New_York",
    )


def artifact(kind: str = "bars", artifact_id: str = "artifact-bars") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        kind=kind,
        sha256=HASH,
        media_type="application/json",
        size_bytes=24,
        created_at=NOW,
    )


def evidence(artifact_id: str = "artifact-bars", evidence_type: str = "bar") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"evidence-{artifact_id}",
        evidence_type=evidence_type,
        artifact_id=artifact_id,
        source="fixture",
        observed_at=NOW,
        trust_level="high",
    )


def data_evidence() -> DataEvidence:
    request = DataEvidenceRequest(
        symbols=["qqq"],
        time_window=window(),
        features=["return_1d"],
        baseline_window=20,
    )
    return DataEvidence(
        request=request,
        bar_artifact=artifact(),
        summary="QQQ has sufficient bars for the requested window.",
        features=[DataFeature(name="return_1d", value=0.02, source_window=window())],
        quality=DataQuality(),
        provider_refs=[ProviderReference(provider_name="csv_demo", request_id="req-1", observed_at=NOW)],
        evidence_refs=[evidence()],
    )


def news_evidence() -> NewsEvidence:
    news_artifact = artifact("news_body", "artifact-news")
    news_ref = evidence("artifact-news", "news")
    request = NewsEvidenceRequest(symbols=["QQQ"], time_window=window(), topics=["earnings"])
    return NewsEvidence(
        request=request,
        clusters=[NewsCluster(cluster_id="cluster-1", headline="Earnings update", news_ids=["news-1"], evidence_refs=[news_ref])],
        source_count=1,
        coverage=NewsCoverage(requested_symbol_count=1, covered_symbol_count=1, source_count=1),
        artifact_refs=[news_artifact],
        evidence_refs=[news_ref],
    )


def test_all_public_contracts_round_trip() -> None:
    request = ResearchRequest(
        request_id="request-1",
        question="Generate a research report for QQQ.",
        symbols=["qqq"],
        time_window=window(),
        report_type="full",
        constraints=ResearchConstraints(allow_mcp=True),
    )
    task = AgentTask(task_id="task-1", request=request, budget=ExecutionBudget(), created_at=NOW, updated_at=NOW)
    assert task.budget.max_model_calls == 4
    first_step = AgentStep(step_id="step-data", actor="orchestrator")
    second_step = AgentStep(step_id="step-report", actor="report", depends_on=["step-data"])
    plan = AgentPlan(plan_id="plan-1", task_id=task.task_id, steps=[first_step, second_step], reason="Collect evidence first.")
    message = AgentMessage(
        message_id="message-1",
        task_id=task.task_id,
        sender="orchestrator",
        recipient="report",
        summary="Evidence is ready.",
        artifact_refs=[artifact()],
        evidence_refs=[evidence()],
        created_at=NOW,
    )
    tool_request = ToolRequest(
        call_id="call-1",
        task_id=task.task_id,
        tool_name="query_bars",
        arguments={"symbol": "QQQ", "limit": 20},
        caller="orchestrator",
        deadline_at=NOW + timedelta(minutes=5),
    )
    tool_result = ToolResult(call_id="call-1", status="succeeded", summary="20 bars loaded.", evidence_refs=[evidence()], artifact_refs=[artifact()])
    data = data_evidence()
    news = news_evidence()
    bundle = EvidenceBundle(task_id=task.task_id, artifact_refs=[artifact()], evidence_refs=[evidence()])
    discovery = SignalDiscoveryInput(
        goal="Find a price and volume signal.",
        data_evidence=[data],
        history_artifacts=[artifact()],
        existing_signals=[ExistingSignal(signal_id="signal-1", version=1, name="baseline", feature_fingerprint="fp-1", status="active")],
    )
    proposal = SignalProposal(
        proposal_id="proposal-1",
        hypothesis="A volume surge after a price drawdown may identify attention changes.",
        features=[SignalFeature(name="volume_ratio", source="market", description="Current volume divided by baseline.")],
        logic_spec="volume_ratio > threshold after a negative return",
        expected_behavior="Marks statistically unusual attention windows.",
        invalidation_conditions=["insufficient bars"],
        minimum_history_bars=20,
        applicable_symbols=["QQQ"],
        evidence_refs=[evidence()],
    )
    candidate_artifact = artifact("candidate_source", "artifact-candidate")
    candidate = CandidateFunction(
        candidate_id="candidate-1",
        proposal_id=proposal.proposal_id,
        interface_version="1",
        source_artifact=candidate_artifact,
        source_hash=HASH,
    )
    validation = SignalValidationResult(
        validation_id="validation-1",
        candidate_id=candidate.candidate_id,
        dataset_refs=[artifact()],
        split_results=[ValidationSplitResult(split_name="holdout", time_window=window(), sample_count=50, observation_count=3, deterministic=True, error_rate=0)],
        leakage_checks=[LeakageCheck(name="no_look_ahead", passed=True, details="No future timestamps accessed.")],
        stability=StabilityResult(passed=True, coverage=0.3),
        decision="pass",
    )
    version = SignalVersion(
        signal_id="signal-1",
        version=2,
        status="active",
        source_hash=HASH,
        validation_id=validation.validation_id,
        approved_by="admin",
        approved_at=NOW,
    )
    observation = SignalObservation(
        signal_id=version.signal_id,
        version=version.version,
        symbol="QQQ",
        timestamp=NOW,
        label="positive",
        strength=0.7,
        confidence=0.6,
        reason="Volume ratio is elevated.",
        evidence_refs=[evidence()],
    )
    approval = SignalApproval(
        approval_id="approval-1",
        signal_id=version.signal_id,
        version=version.version,
        decision="approved",
        decided_by="admin",
        decided_at=NOW,
    )
    anomaly = AnomalyAnalysis(
        analysis_id="anomaly-1",
        metrics=[AnalysisMetric(name="volume_ratio", value=2.1, baseline=1.0, evidence_refs=[evidence()])],
        baseline="20-session baseline",
        candidate_causes=[CandidateCause(description="News attention may contribute.", confidence=0.3)],
        confidence=0.4,
        evidence_refs=[evidence()],
        created_at=NOW,
    )
    macro = MacroAnalysis(
        analysis_id="macro-1",
        events=[MacroEvent(event_id="event-1", description="Rate announcement", occurred_at=NOW, evidence_refs=[evidence()])],
        transmission_paths=[TransmissionPath(event_id="event-1", intermediate_variable="discount rate", affected_scope="technology", expected_window="weeks", confidence=0.4, evidence_refs=[evidence()])],
        affected_scope=["technology"],
        alternative_scenarios=[
            MacroScenario(name="base", description="Rates remain unchanged.", evidence_refs=[evidence()]),
            MacroScenario(name="alternative", description="Rates increase.", evidence_refs=[evidence()]),
        ],
        confidence=0.4,
        evidence_refs=[evidence()],
        created_at=NOW,
    )
    claim = ReportClaim(claim_id="claim-1", text="Volume was above its baseline.", claim_type="fact", confidence=0.9, evidence_refs=[evidence()])
    draft = ReportDraft(
        draft_id="draft-1",
        task_id=task.task_id,
        summary="Research summary.",
        sections=[ReportSection(title="Summary", claim_ids=[claim.claim_id], content="Volume was above its baseline.")],
        claims=[claim],
        generated_at=NOW,
    )
    report_validation = ReportValidationResult(status="passed", claim_results=[ClaimValidationResult(claim_id=claim.claim_id, status="passed")])
    final_report = FinalReport(report_id="report-1", draft=draft, validation=report_validation, published_at=NOW)

    models = [
        request, task, plan, message, tool_request, tool_result, data, news, bundle, discovery,
        proposal, candidate, validation, version, approval, observation, anomaly, macro, draft, final_report,
    ]
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json()) == model


def test_plan_rejects_unknown_or_cyclic_dependencies() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies"):
        AgentPlan(
            plan_id="plan-1",
            task_id="task-1",
            reason="invalid",
            steps=[AgentStep(step_id="step-1", actor="orchestrator", depends_on=["missing"])],
        )

    with pytest.raises(ValidationError, match="cycle"):
        AgentPlan(
            plan_id="plan-1",
            task_id="task-1",
            reason="invalid",
            steps=[
                AgentStep(step_id="step-1", actor="orchestrator", depends_on=["step-2"]),
                AgentStep(step_id="step-2", actor="report", depends_on=["step-1"]),
            ],
        )


def test_signal_discovery_requires_data_and_bars_artifact() -> None:
    with pytest.raises(ValidationError):
        SignalDiscoveryInput(goal="Find a signal.", data_evidence=[], history_artifacts=[artifact()])

    with pytest.raises(ValidationError, match="bars artifacts"):
        SignalDiscoveryInput(goal="Find a signal.", data_evidence=[data_evidence()], history_artifacts=[artifact("news_body")])


def test_news_signal_requires_news_evidence_and_opt_in() -> None:
    discovery = SignalDiscoveryInput(
        goal="Find a news signal.",
        data_evidence=[data_evidence()],
        history_artifacts=[artifact()],
    )
    proposal = SignalProposal(
        proposal_id="proposal-news",
        hypothesis="News changes attention.",
        features=[SignalFeature(name="news_count", source="news", description="News count in window.")],
        logic_spec="news_count > 2",
        expected_behavior="Marks event-heavy windows.",
        invalidation_conditions=["no news coverage"],
        minimum_history_bars=20,
        applicable_symbols=["QQQ"],
        evidence_refs=[evidence()],
    )
    with pytest.raises(ValueError, match="news_evidence"):
        proposal.validate_discovery_input(discovery)

    enabled = discovery.model_copy(
        update={
            "news_evidence": [news_evidence()],
            "constraints": SignalDiscoveryConstraints(allow_news_features=True),
        }
    )
    proposal.validate_discovery_input(enabled)


def test_claims_and_active_versions_require_evidence_and_approval() -> None:
    with pytest.raises(ValidationError):
        ReportClaim(claim_id="claim-1", text="Unsupported.", claim_type="fact", confidence=0.1, evidence_refs=[])

    with pytest.raises(ValidationError, match="requires an approver"):
        SignalVersion(signal_id="signal-1", version=1, status="active", source_hash=HASH, validation_id="validation-1")


@pytest.mark.parametrize(
    ("direction", "label"),
    [("buy_watch", "positive"), ("sell_watch", "negative"), ("observe", "neutral")],
)
def test_legacy_signal_maps_to_research_label(direction: str, label: str) -> None:
    legacy = Signal(
        signal_id="legacy-1",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=NOW,
        direction=direction,  # type: ignore[arg-type]
        strength=0.5,
        confidence=0.6,
        reason="Legacy fixture.",
        trace_id="trace-1",
        created_at=NOW,
    )
    observation = SignalObservation.from_legacy_signal(legacy, version=1, evidence_refs=[evidence()])
    assert observation.label == label


def test_strict_contracts_reject_extra_fields_and_invalid_agent_role() -> None:
    with pytest.raises(ValidationError):
        ResearchConstraints.model_validate({"unknown": True})

    with pytest.raises(ValidationError):
        AgentStep(step_id="step-1", actor="data_agent")


def test_tool_results_require_consistent_error_state() -> None:
    with pytest.raises(ValidationError):
        ToolResult(call_id="call-1", status="failed", summary="failed")

    ToolResult(
        call_id="call-1",
        status="failed",
        summary="failed",
        error=ToolError(code="provider_unavailable", message="fixture"),
        retryable=True,
    )
