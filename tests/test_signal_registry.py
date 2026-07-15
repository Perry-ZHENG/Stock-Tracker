from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.bars.validation import generate_bar_id
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.signals import SignalFeature, SignalProposal
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.schemas import Bar
from stock_agent.signal_lab.candidate_builder import CandidateBuildInput, CandidateBuilder
from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG
from stock_agent.signal_lab.validation import SignalValidationInput, SignalValidator, ValidationPolicy
from stock_agent.signals.approval import ApprovalRequest, SignalApprovalService
from stock_agent.signals.registry import SignalRegistry, SignalRegistryError
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 8, 8, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=20), to_ts=NOW, timezone="America/New_York")


class ScriptedModel:
    def __init__(self, source: str) -> None:
        self.source = source

    def __call__(self, _prompt: str) -> str:
        return json.dumps({"interface_version": "signal_context_v1", "required_features": ["return_change"], "source_code": self.source})


def test_registry_requires_passing_validation_and_human_admin_for_activation(tmp_path: Path) -> None:
    connection, service, candidate, validation = _validated_candidate(tmp_path, "candidate-1")
    registry = SignalRegistry(repository=SignalRepository(connection), artifact_service=service)
    version = registry.register_validated(
        signal_id="signal-return",
        name="return observation",
        feature_fingerprint="fingerprint-return",
        candidate=candidate,
        validation=validation,
        now=NOW,
    )
    approval_service = SignalApprovalService(registry=registry, admin_ids={"admin-1"})

    assert version.status == "validated"
    with pytest.raises(SignalRegistryError, match="human admin"):
        approval_service.approve(
            ApprovalRequest(signal_id="signal-return", version=1, decided_by="signal-agent", actor_role="signal_discovery", reason="auto"),
            now=NOW,
        )
    active, approval = approval_service.approve(
        ApprovalRequest(signal_id="signal-return", version=1, decided_by="admin-1", actor_role="admin", reason="reviewed evidence"),
        now=NOW,
    )

    assert active.status == "active"
    assert approval.decided_by == "admin-1"
    assert registry.show("signal-return")[0].status == "active"
    connection.close()


def test_registry_switches_active_versions_and_rolls_back_transactionally(tmp_path: Path) -> None:
    connection, service, candidate_one, validation_one = _validated_candidate(tmp_path, "candidate-1")
    registry = SignalRegistry(repository=SignalRepository(connection), artifact_service=service)
    approvals = SignalApprovalService(registry=registry, admin_ids={"admin-1"})
    registry.register_validated(
        signal_id="signal-return", name="return observation", feature_fingerprint="fingerprint-return", candidate=candidate_one, validation=validation_one, now=NOW
    )
    approvals.approve(ApprovalRequest(signal_id="signal-return", version=1, decided_by="admin-1", actor_role="admin", reason="v1"), now=NOW)
    candidate_two, validation_two = _next_validated_candidate(service, candidate_one, suffix="two")
    version_two = registry.register_validated(
        signal_id="signal-return", name="return observation", feature_fingerprint="fingerprint-return", candidate=candidate_two, validation=validation_two, now=NOW
    )
    approvals.approve(ApprovalRequest(signal_id="signal-return", version=2, decided_by="admin-1", actor_role="admin", reason="v2"), now=NOW)
    rolled_back = registry.rollback(signal_id="signal-return", target_version=1, approved_by="admin-1", now=NOW)

    assert version_two.status == "validated"
    assert registry.repository.get_version("signal-return", 2).status == "suspended"
    assert rolled_back.status == "active"
    assert registry.diff("signal-return", 1, 2)["source_hash_changed"]
    connection.close()


def test_registry_rejects_hash_changes_and_failed_validation(tmp_path: Path) -> None:
    connection, service, candidate, validation = _validated_candidate(tmp_path, "candidate-1")
    registry = SignalRegistry(repository=SignalRepository(connection), artifact_service=service)
    failed = validation.model_copy(update={"decision": "reject"})

    with pytest.raises(SignalRegistryError, match="passing validation"):
        registry.register_validated(
            signal_id="signal-return", name="return observation", feature_fingerprint="fingerprint-return", candidate=candidate, validation=failed, now=NOW
        )
    with pytest.raises(SignalRegistryError, match="persisted"):
        registry.register_validated(
            signal_id="signal-return", name="return observation", feature_fingerprint="fingerprint-return", candidate=candidate.model_copy(update={"source_hash": "b" * 64}), validation=validation, now=NOW
        )
    connection.close()


def _validated_candidate(tmp_path: Path, candidate_id: str):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(AgentTask(task_id="task-registry", request=ResearchRequest(request_id="request-registry", question="Validate registry candidate.", symbols=["QQQ"], time_window=WINDOW), created_at=NOW, updated_at=NOW))
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    bars = [_bar(index) for index in range(12)]
    dataset = service.save_json("task-registry", kind="bars", payload={"bars": [bar.model_dump(mode="json") for bar in bars]}, source="fixture-bars", created_at=NOW)
    evidence = EvidenceService(connection, service.store).create("task-registry", artifact=dataset, evidence_type="bar", source="fixture-bars", observed_at=NOW, evidence_id="evidence-bars")
    proposal = SignalProposal(proposal_id="proposal-registry", hypothesis="Positive return condition.", features=[SignalFeature(name="return_change", source="market", description="Verified return.")], logic_spec="return_change positive", expected_behavior="research observation", invalidation_conditions=["insufficient history"], minimum_history_bars=2, applicable_symbols=["QQQ"], evidence_refs=[evidence])
    candidate = CandidateBuilder(model_client=ScriptedModel(_source(candidate_id)), artifact_service=service).build("task-registry", CandidateBuildInput(proposal=proposal, feature_catalog=DEFAULT_FEATURE_CATALOG, history_artifact=dataset, model_id="fixture-model"), candidate_id=candidate_id, now=NOW).candidate
    validation = SignalValidator(artifact_service=service).validate("task-registry", SignalValidationInput(candidate=candidate, dataset_artifacts=[dataset], time_window=WINDOW, symbols=["QQQ"], policy=ValidationPolicy(min_bars_per_split=2, min_coverage=0.1)), validation_id=f"validation-{candidate_id}", now=NOW)
    return connection, service, candidate, validation


def _next_validated_candidate(service: ArtifactService, prior, *, suffix: str):
    repository = SignalRepository(service.store.connection)
    provenance = repository.get_build_provenance(prior.candidate_id)
    assert provenance is not None
    candidate = CandidateBuilder(model_client=ScriptedModel(_source(suffix)), artifact_service=service, repository=repository).build("task-registry", CandidateBuildInput(proposal=provenance.proposal, feature_catalog=provenance.feature_catalog, history_artifact=provenance.history_artifact, model_id="fixture-model"), candidate_id=f"candidate-{suffix}", now=NOW).candidate
    validation = SignalValidator(artifact_service=service, repository=repository).validate("task-registry", SignalValidationInput(candidate=candidate, dataset_artifacts=[provenance.history_artifact], time_window=WINDOW, symbols=["QQQ"], policy=ValidationPolicy(min_bars_per_split=2, min_coverage=0.1)), validation_id=f"validation-{suffix}", now=NOW)
    return candidate, validation


def _bar(index: int) -> Bar:
    timestamp = NOW - timedelta(days=12 - index)
    close = 100 + index
    source = "fixture-registry"
    return Bar(bar_id=generate_bar_id("QQQ", "1d", timestamp.isoformat().replace("+00:00", "Z"), source), symbol="QQQ", timestamp=timestamp, interval="1d", open=close - 0.1, high=close + 0.2, low=close - 0.2, close=close, volume=1_000 + index, source=source)


def _source(suffix: str) -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    return [{'timestamp': timestamp, 'label': 'positive', 'strength': value, 'confidence': 0.5, 'reason': 'positive " + suffix + "'} for timestamp, value in zip(context.timestamps, values) if value > 0]\n"
