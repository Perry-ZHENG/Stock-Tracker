from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_agent.agents.signal_discovery import SignalDiscoveryAgent
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import DataEvidence, DataFeature, DataQuality, NewsCoverage, NewsEvidence, ProviderReference
from stock_agent.contracts.signals import ExistingSignal, SignalDiscoveryConstraints, SignalDiscoveryInput
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.signals.duplicate_detection import proposal_fingerprint
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(
    from_ts=NOW - timedelta(days=1),
    to_ts=NOW,
    timezone="America/New_York",
)


class ScriptedModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        return self.output


def test_signal_discovery_generates_reproducible_evidence_backed_proposal(tmp_path: Path) -> None:
    connection, service, data, history, data_ref, _news = _inputs(tmp_path)
    model = ScriptedModel(_proposal_json(data_ref))
    agent = SignalDiscoveryAgent(model_client=model, artifact_service=service)
    discovery_input = SignalDiscoveryInput(
        goal="Find a volume-confirmed continuation hypothesis.",
        data_evidence=[data],
        history_artifacts=[history],
    )

    first = agent.discover("task-signal", discovery_input, now=NOW)
    second = agent.discover("task-signal", discovery_input, now=NOW)

    assert first.proposal is not None and second.proposal == first.proposal
    assert first.proposal.evidence_refs == [data_ref]
    assert model.calls == 2
    connection.close()


def test_signal_discovery_requires_structured_inputs_and_requests_missing_history(tmp_path: Path) -> None:
    connection, service, data, history, data_ref, _news = _inputs(tmp_path, baseline_insufficient=True)
    model = ScriptedModel(_proposal_json(data_ref))
    agent = SignalDiscoveryAgent(model_client=model, artifact_service=service)
    with pytest.raises(ValidationError):
        SignalDiscoveryInput(goal="missing data", data_evidence=[], history_artifacts=[history])
    with pytest.raises(ValidationError):
        SignalDiscoveryInput(goal="missing history", data_evidence=[data], history_artifacts=[])

    result = agent.discover(
        "task-signal",
        SignalDiscoveryInput(goal="insufficient baseline", data_evidence=[data], history_artifacts=[history]),
        now=NOW,
    )

    assert result.evidence_gap is not None
    assert result.evidence_gap.missing_evidence_types == ["bar"]
    assert model.calls == 0
    connection.close()


def test_signal_discovery_rejects_unknown_evidence_future_features_and_duplicates(tmp_path: Path) -> None:
    connection, service, data, history, data_ref, _news = _inputs(tmp_path)
    discovery_input = SignalDiscoveryInput(goal="test", data_evidence=[data], history_artifacts=[history])

    hallucinated = SignalDiscoveryAgent(
        model_client=ScriptedModel(_proposal_json("evidence-invented")), artifact_service=service
    ).discover("task-signal", discovery_input, now=NOW)
    future = SignalDiscoveryAgent(
        model_client=ScriptedModel(_proposal_json(data_ref, logic_spec="use next bar close")), artifact_service=service
    ).discover("task-signal", discovery_input, now=NOW)
    proposal = _proposal_model(data_ref)
    duplicate_input = discovery_input.model_copy(
        update={
            "existing_signals": [
                ExistingSignal(
                    signal_id="signal-active",
                    version=1,
                    name="different name",
                    feature_fingerprint=proposal_fingerprint(proposal),
                    status="active",
                )
            ]
        }
    )
    duplicate = SignalDiscoveryAgent(
        model_client=ScriptedModel(_proposal_json(data_ref)), artifact_service=service
    ).discover("task-signal", duplicate_input, now=NOW)

    assert hallucinated.no_proposal is not None and hallucinated.no_proposal.reason_code == "unknown_evidence_reference"
    assert future.no_proposal is not None and future.no_proposal.reason_code == "future_feature_forbidden"
    assert duplicate.no_proposal is not None and duplicate.no_proposal.reuse_signal_id == "signal-active"
    connection.close()


def test_signal_discovery_enforces_news_evidence_and_revision_limits(tmp_path: Path) -> None:
    connection, service, data, history, data_ref, news = _inputs(tmp_path, with_news=True)
    news_proposal = _proposal_json(data_ref, feature_source="news")
    without_news = SignalDiscoveryAgent(model_client=ScriptedModel(news_proposal), artifact_service=service).discover(
        "task-signal",
        SignalDiscoveryInput(goal="news feature", data_evidence=[data], history_artifacts=[history]),
        now=NOW,
    )
    wrong_news_reference = SignalDiscoveryAgent(model_client=ScriptedModel(news_proposal), artifact_service=service).discover(
        "task-signal",
        SignalDiscoveryInput(
            goal="news feature",
            data_evidence=[data],
            history_artifacts=[history],
            news_evidence=[news],
            constraints=SignalDiscoveryConstraints(allow_news_features=True),
        ),
        now=NOW,
    )
    revision_limited = SignalDiscoveryAgent(model_client=ScriptedModel("not-json"), artifact_service=service).discover(
        "task-signal",
        SignalDiscoveryInput(
            goal="revision limit",
            data_evidence=[data],
            history_artifacts=[history],
            validation_feedback=[
                {
                    "candidate_id": "candidate-1",
                    "decision": "revise",
                    "reasons": ["need more stability"],
                }
            ],
            constraints=SignalDiscoveryConstraints(max_revisions=1),
        ),
        now=NOW,
    )

    assert without_news.no_proposal is not None and without_news.no_proposal.reason_code == "proposal_constraint_failed"
    assert wrong_news_reference.no_proposal is not None and wrong_news_reference.no_proposal.reason_code == "news_feature_without_news_reference"
    assert revision_limited.no_proposal is not None and revision_limited.no_proposal.reason_code == "revision_budget_exhausted"
    connection.close()


def test_signal_discovery_requires_parent_and_change_summary_for_revision(tmp_path: Path) -> None:
    connection, service, data, history, data_ref, _news = _inputs(tmp_path)
    discovery_input = SignalDiscoveryInput(
        goal="revise hypothesis",
        data_evidence=[data],
        history_artifacts=[history],
        validation_feedback=[
            {"candidate_id": "candidate-parent", "decision": "revise", "reasons": ["reduce complexity"]}
        ],
        constraints=SignalDiscoveryConstraints(max_revisions=2),
    )
    missing_parent = SignalDiscoveryAgent(
        model_client=ScriptedModel(_proposal_json(data_ref)), artifact_service=service
    ).discover("task-signal", discovery_input, now=NOW)
    revised = SignalDiscoveryAgent(
        model_client=ScriptedModel(
            _proposal_json(
                data_ref,
                parent_candidate_id="candidate-parent",
                revision_summary="Removed unsupported feature from the prior candidate.",
            )
        ),
        artifact_service=service,
    ).discover("task-signal", discovery_input, now=NOW)

    assert missing_parent.no_proposal is not None and missing_parent.no_proposal.reason_code == "proposal_constraint_failed"
    assert revised.proposal is not None and revised.proposal.parent_candidate_id == "candidate-parent"
    connection.close()


def _inputs(tmp_path: Path, *, baseline_insufficient: bool = False, with_news: bool = False):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-signal",
            request=ResearchRequest(
                request_id="request-signal",
                question="Discover a research signal.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    history = service.save_json(
        "task-signal",
        kind="bars",
        payload={"bars": [{"symbol": "QQQ", "close": 100 + index} for index in range(8)]},
        source="fixture-data",
        created_at=NOW,
    )
    evidence_service = EvidenceService(connection, service.store)
    data_ref = evidence_service.create(
        "task-signal",
        artifact=history,
        evidence_type="bar",
        source="fixture-data",
        observed_at=NOW,
        evidence_id="evidence-data",
    )
    data = DataEvidence(
        request={"symbols": ["QQQ"], "time_window": WINDOW, "baseline_window": 3},
        bar_artifact=history,
        summary="Verified QQQ data.",
        features=[DataFeature(name="QQQ.return_change", value=0.02, source_window=WINDOW)],
        quality=DataQuality(flags=["baseline_insufficient:QQQ"] if baseline_insufficient else []),
        provider_refs=[ProviderReference(provider_name="fixture", request_id="request-data", observed_at=NOW)],
        evidence_refs=[data_ref],
    )
    news = NewsEvidence(
        request={"symbols": ["QQQ"], "time_window": WINDOW},
        source_count=0,
        coverage=NewsCoverage(requested_symbol_count=1, covered_symbol_count=0, source_count=0),
    )
    if with_news:
        news_artifact = service.save_json(
            "task-signal",
            kind="news_body",
            payload={"symbol": "QQQ", "published_at": NOW.isoformat(), "summary": "Volume news."},
            source="fixture-news",
            created_at=NOW,
        )
        news_ref = evidence_service.create(
            "task-signal",
            artifact=news_artifact,
            evidence_type="news",
            source="fixture-news",
            observed_at=NOW,
            evidence_id="evidence-news",
        )
        news = news.model_copy(update={"artifact_refs": [news_artifact], "evidence_refs": [news_ref], "source_count": 1})
    return connection, service, data, history, data_ref, news


def _proposal_model(reference):
    from stock_agent.contracts.signals import SignalProposal

    return SignalProposal.model_validate_json(_proposal_json(reference))


def _proposal_json(
    reference,
    *,
    feature_source: str = "market",
    logic_spec: str = "return_change > 0",
    parent_candidate_id: str | None = None,
    revision_summary: str | None = None,
) -> str:
    evidence_payload = (
        reference.model_dump(mode="json")
        if hasattr(reference, "model_dump")
        else {
            "evidence_id": reference,
            "evidence_type": "bar",
            "artifact_id": "artifact-placeholder",
            "source": "fixture-data",
            "observed_at": NOW.isoformat(),
        }
    )
    return json.dumps(
        {
            "proposal_id": "proposal-volume-return",
            "hypothesis": "Volume confirmed return continuation",
            "features": [
                {
                    "name": "return_change",
                    "source": feature_source,
                    "description": "verified return feature",
                }
            ],
            "logic_spec": logic_spec,
            "expected_behavior": "a qualified continuation hypothesis",
            "invalidation_conditions": ["return reverses under normal volume"],
            "minimum_history_bars": 3,
            "applicable_symbols": ["QQQ"],
            "evidence_refs": [evidence_payload],
            "parent_candidate_id": parent_candidate_id,
            "revision_summary": revision_summary,
        }
    )
