from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.signals import SignalFeature, SignalProposal
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.signal_lab.candidate_builder import CandidateBuildError, CandidateBuildInput, CandidateBuilder
from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 5, 5, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=20), to_ts=NOW, timezone="America/New_York")


class ScriptedModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        return self.output


def _draft(source: str, *, required_features: list[str] | None = None) -> str:
    return json.dumps(
        {
            "interface_version": "signal_context_v1",
            "required_features": required_features or ["return_change"],
            "source_code": source,
        }
    )


def _source(*, reason: str = "positive return") -> str:
    return (
        "def compute(context):\n"
        "    values = context.features['return_change']\n"
        "    points = []\n"
        "    for timestamp, value in zip(context.timestamps, values):\n"
        "        if value > 0:\n"
        "            points.append({'timestamp': timestamp, 'label': 'positive', 'strength': min(value, 1.0), "
        f"'confidence': 0.5, 'reason': '{reason}'}})\n"
        "    return points\n"
    )


def test_candidate_builder_creates_normalized_source_artifact_and_complete_provenance(tmp_path: Path) -> None:
    connection, service, build_input = _input(tmp_path)
    model = ScriptedModel(_draft(_source()))
    repository = SignalRepository(connection)

    result = CandidateBuilder(model_client=model, artifact_service=service, repository=repository).build(
        "task-candidate", build_input, candidate_id="candidate-1", now=NOW
    )

    assert result.candidate.source_artifact.kind == "candidate_source"
    assert result.candidate.source_hash == result.candidate.source_artifact.sha256
    assert result.candidate.dependencies == []
    assert result.provenance.proposal == build_input.proposal
    assert result.provenance.feature_catalog == DEFAULT_FEATURE_CATALOG
    assert repository.get_candidate("candidate-1") == result.candidate
    assert repository.get_build_provenance("candidate-1") == result.provenance
    source = service.open_bytes("task-candidate", result.candidate.source_artifact).decode("utf-8")
    assert source.startswith("def compute(context):\n")
    assert "context.features['return_change']" in source
    assert model.calls == 1
    connection.close()


def test_candidate_builder_identifies_repeated_build_inputs_without_assuming_same_source(tmp_path: Path) -> None:
    connection, service, build_input = _input(tmp_path)
    repository = SignalRepository(connection)
    first = CandidateBuilder(
        model_client=ScriptedModel(_draft(_source())), artifact_service=service, repository=repository
    ).build("task-candidate", build_input, candidate_id="candidate-1", now=NOW)
    second = CandidateBuilder(
        model_client=ScriptedModel(_draft(_source(reason="positive return variant"))), artifact_service=service, repository=repository
    ).build("task-candidate", build_input, candidate_id="candidate-2", now=NOW)

    assert first.prior_candidate_ids == []
    assert second.prior_candidate_ids == ["candidate-1"]
    assert first.candidate.source_hash != second.candidate.source_hash
    connection.close()


@pytest.mark.parametrize(
    ("draft", "message"),
    [
        (_draft(_source(), required_features=["unknown_feature"]), "FeatureCatalog"),
        (_draft("def calculate(context):\n    return []\n"), "signature"),
        (_draft("import os\n\ndef compute(context):\n    return []\n"), "compute function"),
        (_draft("def compute(context):\n    order = 1\n    return []\n"), "forbidden"),
        ("not-json", "valid CandidateFunctionDraft"),
    ],
)
def test_candidate_builder_rejects_unknown_features_unsafe_source_and_non_json(
    tmp_path: Path,
    draft: str,
    message: str,
) -> None:
    connection, service, build_input = _input(tmp_path)

    with pytest.raises(CandidateBuildError, match=message):
        CandidateBuilder(model_client=ScriptedModel(draft), artifact_service=service).build(
            "task-candidate", build_input, candidate_id="candidate-rejected", now=NOW
        )
    connection.close()


def test_candidate_builder_requires_persisted_proposal_evidence_before_model_execution(tmp_path: Path) -> None:
    connection, service, build_input = _input(tmp_path)
    missing_ref = build_input.proposal.evidence_refs[0].model_copy(update={"evidence_id": "evidence-missing"})
    invalid_input = build_input.model_copy(
        update={"proposal": build_input.proposal.model_copy(update={"evidence_refs": [missing_ref]})}
    )
    model = ScriptedModel(_draft(_source()))

    with pytest.raises(CandidateBuildError, match="proposal evidence"):
        CandidateBuilder(model_client=model, artifact_service=service).build(
            "task-candidate", invalid_input, candidate_id="candidate-missing", now=NOW
        )

    assert model.calls == 0
    connection.close()


def _input(tmp_path: Path) -> tuple[object, ArtifactService, CandidateBuildInput]:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-candidate",
            request=ResearchRequest(
                request_id="request-candidate",
                question="Build an evidence-backed research signal candidate.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    history = service.save_json(
        "task-candidate",
        kind="bars",
        payload={"bars": [{"symbol": "QQQ", "timestamp": NOW.isoformat()}]},
        source="fixture-bars",
        created_at=NOW,
    )
    evidence = EvidenceService(connection, service.store).create(
        "task-candidate",
        artifact=history,
        evidence_type="bar",
        source="fixture-bars",
        observed_at=NOW,
        evidence_id="evidence-bars",
    )
    proposal = SignalProposal(
        proposal_id="proposal-return",
        hypothesis="Positive return change may identify a research window.",
        features=[SignalFeature(name="return_change", source="market", description="Verified bar return change.")],
        logic_spec="return_change is positive",
        expected_behavior="Marks positive return observations for later research.",
        invalidation_conditions=["insufficient data"],
        minimum_history_bars=2,
        applicable_symbols=["QQQ"],
        evidence_refs=[evidence],
    )
    return (
        connection,
        service,
        CandidateBuildInput(
            proposal=proposal,
            feature_catalog=DEFAULT_FEATURE_CATALOG,
            history_artifact=history,
            model_id="fixture-model-v1",
        ),
    )
