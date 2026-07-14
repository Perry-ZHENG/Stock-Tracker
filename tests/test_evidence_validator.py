from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.validation.evidence import EvidenceValidator


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


def test_evidence_validator_resolves_only_current_task_owned_artifacts(tmp_path: Path) -> None:
    connection, service, evidence_service, reference, artifact = _setup(tmp_path)
    validator = EvidenceValidator(evidence_service)
    bundle = EvidenceBundle(task_id="task-evidence", artifact_refs=[artifact], evidence_refs=[reference])

    materials, issues = validator.resolve("task-evidence", [reference], bundle, now=NOW)

    assert issues == []
    assert materials[0].symbols == frozenset({"QQQ"})
    assert materials[0].numbers == (101.0, 10_000.0)
    assert materials[0].timestamps[0].date().isoformat() == "2027-01-02"
    connection.close()


def test_evidence_validator_rejects_expired_cross_task_and_forged_references(tmp_path: Path) -> None:
    connection, service, evidence_service, reference, artifact = _setup(tmp_path, expires_at=NOW + timedelta(minutes=5))
    validator = EvidenceValidator(evidence_service)
    valid_bundle = EvidenceBundle(task_id="task-evidence", artifact_refs=[artifact], evidence_refs=[reference])
    forged = reference.model_copy(update={"source": "spoofed-source"})
    forged_bundle = EvidenceBundle(task_id="task-evidence", artifact_refs=[artifact], evidence_refs=[forged])
    cross_bundle = EvidenceBundle(task_id="task-other", artifact_refs=[artifact], evidence_refs=[reference])

    _materials, expired = validator.resolve("task-evidence", [reference], valid_bundle, now=NOW + timedelta(hours=1))
    _materials, fake = validator.resolve("task-evidence", [forged], forged_bundle, now=NOW)
    _materials, cross = validator.resolve("task-other", [reference], cross_bundle, now=NOW)

    assert expired == [f"evidence_unavailable:{reference.evidence_id}"]
    assert fake == [f"forged_or_mismatched_evidence:{reference.evidence_id}"]
    assert cross == [f"evidence_unavailable:{reference.evidence_id}"]
    connection.close()


def _setup(tmp_path: Path, *, expires_at: datetime | None = None):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    _create_task(repository, "task-evidence")
    _create_task(repository, "task-other")
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    artifact = service.save_json(
        "task-evidence",
        kind="bars",
        payload={
            "bars": [
                {
                    "symbol": "QQQ",
                    "timestamp": "2027-01-02T19:30:00Z",
                    "close": 101.0,
                    "volume": 10_000,
                }
            ]
        },
        source="fixture",
        created_at=NOW,
        expires_at=expires_at,
    )
    evidence_service = EvidenceService(connection, service.store)
    reference = evidence_service.create(
        "task-evidence",
        artifact=artifact,
        evidence_type="bar",
        source="fixture",
        observed_at=NOW,
        valid_until=expires_at,
        evidence_id="evidence-bars",
    )
    return connection, service, evidence_service, reference, artifact


def _create_task(repository: TaskRepository, task_id: str) -> None:
    repository.create_task(
        AgentTask(
            task_id=task_id,
            request=ResearchRequest(
                request_id=f"request-{task_id}",
                question="Validate report evidence.",
                symbols=["QQQ"],
                time_window=TimeWindow(
                    from_ts=NOW - timedelta(days=1),
                    to_ts=NOW,
                    timezone="America/New_York",
                ),
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
