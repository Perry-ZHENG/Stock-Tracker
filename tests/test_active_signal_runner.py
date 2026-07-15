from __future__ import annotations

from datetime import UTC, datetime

from stock_agent.contracts.evidence import DataEvidence, DataQuality, ProviderReference
from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG
from stock_agent.signals.approval import ApprovalRequest, SignalApprovalService
from stock_agent.signals.registry import SignalRegistry
from stock_agent.signals.runner import ActiveSignalRunner
from stock_agent.storage.signal_repository import SignalRepository

from test_signal_registry import NOW, WINDOW, _validated_candidate


def test_active_runner_only_executes_human_approved_versions(tmp_path) -> None:
    connection, service, candidate, validation = _validated_candidate(tmp_path, "candidate-runner")
    repository = SignalRepository(connection)
    registry = SignalRegistry(repository=repository, artifact_service=service)
    registered = registry.register_validated(
        signal_id="signal-runner", name="return observation", feature_fingerprint="runner-fingerprint", candidate=candidate, validation=validation, now=NOW
    )
    provenance = repository.get_build_provenance(candidate.candidate_id)
    assert provenance is not None
    data = DataEvidence(
        request={"symbols": ["QQQ"], "time_window": WINDOW, "interval": "1d"},
        bar_artifact=provenance.history_artifact,
        summary="Verified runner bars.",
        quality=DataQuality(),
        provider_refs=[ProviderReference(provider_name="fixture", request_id="runner-data", observed_at=NOW)],
        evidence_refs=provenance.proposal.evidence_refs,
    )

    before = ActiveSignalRunner(artifact_service=service, repository=repository).run("task-registry", data, now=NOW)
    SignalApprovalService(registry=registry, admin_ids={"admin-1"}).approve(
        ApprovalRequest(signal_id="signal-runner", version=registered.version, decided_by="admin-1", actor_role="admin", reason="approved"), now=NOW
    )
    after = ActiveSignalRunner(artifact_service=service, repository=repository).run("task-registry", data, now=NOW)

    assert before.active_version_count == 0 and before.observations == []
    assert after.active_version_count == 1
    assert after.observations
    assert all(item.signal_id == "signal-runner" and item.evidence_refs == data.evidence_refs for item in after.observations)
    assert all(trace.status == "succeeded" for trace in after.traces)
    connection.close()
