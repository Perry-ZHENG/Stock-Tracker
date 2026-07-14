from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService, EvidenceServiceError
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)


def test_evidence_service_requires_matching_source_and_builds_valid_bundle(tmp_path: Path) -> None:
    connection, store, repository = _setup(tmp_path)
    _create_task(repository, "task-1")
    artifact = store.put_json(
        "task-1",
        kind="bars",
        payload={"bars": [{"close": 100}]},
        source="csv_demo",
        created_at=NOW,
    )
    service = EvidenceService(connection, store)
    evidence = service.create(
        "task-1",
        artifact=artifact,
        evidence_type="bar",
        source="csv_demo",
        observed_at=NOW,
        trust_level="high",
        evidence_id="evidence-1",
    )

    bundle = service.build_bundle("task-1", [evidence], now=NOW)

    assert bundle.artifact_refs == [artifact]
    assert bundle.evidence_refs == [evidence]
    with pytest.raises(EvidenceServiceError, match="source"):
        service.create(
            "task-1",
            artifact=artifact,
            evidence_type="bar",
            source="untrusted_other_source",
            observed_at=NOW,
        )
    connection.close()


def test_evidence_service_rejects_cross_task_and_expired_references(tmp_path: Path) -> None:
    connection, store, repository = _setup(tmp_path)
    _create_task(repository, "task-1")
    _create_task(repository, "task-2")
    artifact = store.put_json(
        "task-1",
        kind="bars",
        payload={"bars": []},
        source="csv_demo",
        created_at=NOW,
    )
    service = EvidenceService(connection, store)

    with pytest.raises(EvidenceServiceError, match="another task"):
        service.create(
            "task-2",
            artifact=artifact,
            evidence_type="bar",
            source="csv_demo",
            observed_at=NOW,
        )

    evidence = service.create(
        "task-1",
        artifact=artifact,
        evidence_type="bar",
        source="csv_demo",
        observed_at=NOW,
        valid_until=NOW + timedelta(minutes=1),
        evidence_id="evidence-expiring",
    )
    with pytest.raises(EvidenceServiceError, match="expired"):
        service.get("task-1", evidence.evidence_id, now=NOW + timedelta(minutes=2))
    connection.close()


def test_evidence_bundle_rejects_an_expired_artifact(tmp_path: Path) -> None:
    connection, store, repository = _setup(tmp_path)
    _create_task(repository, "task-1")
    artifact = store.put_json(
        "task-1",
        kind="bars",
        payload={"bars": []},
        source="csv_demo",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    service = EvidenceService(connection, store)
    evidence = service.create(
        "task-1",
        artifact=artifact,
        evidence_type="bar",
        source="csv_demo",
        observed_at=NOW,
        evidence_id="evidence-artifact-expiring",
    )

    with pytest.raises(EvidenceServiceError, match="expired"):
        service.build_bundle("task-1", [evidence], now=NOW + timedelta(minutes=2))
    connection.close()


def _setup(tmp_path: Path) -> tuple[object, ArtifactStore, TaskRepository]:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    return connection, ArtifactStore(connection, tmp_path / "lake"), TaskRepository(connection)


def _create_task(repository: TaskRepository, task_id: str) -> None:
    repository.create_task(
        AgentTask(
            task_id=task_id,
            request=ResearchRequest(
                request_id=f"request-{task_id}",
                question="Create evidence.",
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
