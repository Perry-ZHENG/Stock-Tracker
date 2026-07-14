from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import (
    ArtifactAccessError,
    ArtifactIntegrityError,
    ArtifactStore,
    ArtifactValidationError,
)
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.storage.retention import list_expired_artifacts
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)


def test_json_artifacts_are_content_addressed_redacted_and_deduplicated(tmp_path: Path) -> None:
    connection, service, repository = _service(tmp_path)
    _create_task(repository, "task-1")

    first = service.save_json(
        "task-1",
        kind="bars",
        payload={"symbol": "QQQ", "api_key": "secret-value", "note": "token=inline-secret"},
        source="csv_demo",
        created_at=NOW,
    )
    second = service.save_json(
        "task-1",
        kind="bars",
        payload={"symbol": "QQQ", "api_key": "secret-value", "note": "token=inline-secret"},
        source="csv_demo",
        created_at=NOW,
    )
    stored_rows = connection.execute("SELECT COUNT(*) FROM artifacts WHERE task_id = 'task-1'").fetchone()[0]

    assert first == second
    assert stored_rows == 1
    assert service.load_json("task-1", first) == {
        "api_key": "[REDACTED]",
        "note": "token=[REDACTED]",
        "symbol": "QQQ",
    }
    assert first.sha256 == hashlib.sha256(service.open_bytes("task-1", first)).hexdigest()
    connection.close()


def test_artifact_store_enforces_task_isolation_and_payload_policy(tmp_path: Path) -> None:
    connection, service, repository = _service(tmp_path, max_artifact_bytes=32)
    _create_task(repository, "task-1")
    _create_task(repository, "task-2")
    artifact = service.save_bytes(
        "task-1",
        kind="news_body",
        payload=b"short news body",
        media_type="text/plain",
        source="news_provider",
        created_at=NOW,
    )

    with pytest.raises(ArtifactAccessError):
        service.open_bytes("task-2", artifact)
    with pytest.raises(ArtifactValidationError, match="size limit"):
        service.save_bytes(
            "task-1",
            kind="news_body",
            payload=b"x" * 33,
            media_type="text/plain",
            source="news_provider",
            created_at=NOW,
        )
    with pytest.raises(ArtifactValidationError, match="not allowed"):
        service.save_bytes(
            "task-1",
            kind="news_body",
            payload=b"data",
            media_type="application/octet-stream",
            source="news_provider",
            created_at=NOW,
        )
    connection.close()


def test_store_detects_payload_hash_corruption_without_exposing_paths(tmp_path: Path) -> None:
    connection, service, repository = _service(tmp_path)
    _create_task(repository, "task-1")
    artifact = service.save_bytes(
        "task-1",
        kind="candidate_source",
        payload=b"def signal():\n    return 1\n",
        media_type="text/x-python",
        source="signal_discovery",
        created_at=NOW,
    )
    storage_key = connection.execute(
        "SELECT storage_key FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)
    ).fetchone()[0]
    (tmp_path / "lake" / storage_key).write_bytes(b"corrupted")

    with pytest.raises(ArtifactIntegrityError):
        service.open_bytes("task-1", artifact)
    connection.close()


def test_expired_artifact_cleanup_removes_dependent_evidence_and_unreferenced_payload(tmp_path: Path) -> None:
    connection, service, repository = _service(tmp_path)
    _create_task(repository, "task-1")
    artifact = service.save_json(
        "task-1",
        kind="bars",
        payload={"bars": []},
        source="csv_demo",
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    from stock_agent.evidence.service import EvidenceService

    evidence = EvidenceService(connection, service.store).create(
        "task-1",
        artifact=artifact,
        evidence_type="bar",
        source="csv_demo",
        observed_at=NOW,
        valid_until=NOW + timedelta(hours=12),
        evidence_id="evidence-expired",
    )
    assert list_expired_artifacts(connection, now=NOW) == []

    result = service.cleanup_expired(now=NOW + timedelta(days=2))

    assert result.artifact_count == 1
    assert result.evidence_count == 1
    assert result.payload_count == 1
    assert repository.get_artifact("task-1", artifact.artifact_id) is None
    assert repository.get_evidence("task-1", evidence.evidence_id) is None
    connection.close()


def _service(tmp_path: Path, *, max_artifact_bytes: int = 5 * 1024 * 1024) -> tuple[object, ArtifactService, TaskRepository]:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    store = ArtifactStore(connection, tmp_path / "lake", max_artifact_bytes=max_artifact_bytes)
    return connection, ArtifactService(store), TaskRepository(connection)


def _create_task(repository: TaskRepository, task_id: str) -> None:
    repository.create_task(
        AgentTask(
            task_id=task_id,
            request=ResearchRequest(
                request_id=f"request-{task_id}",
                question="Store research evidence.",
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
