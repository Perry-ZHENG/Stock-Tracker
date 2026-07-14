"""Application-facing Artifact service that exposes typed save and read operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_agent.artifacts.store import ArtifactCleanupResult, ArtifactStore
from stock_agent.contracts.evidence import ArtifactKind, ArtifactRef


class ArtifactService:
    """Keep callers on task-scoped APIs instead of exposing filesystem paths."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def save_json(
        self,
        task_id: str,
        *,
        kind: ArtifactKind,
        payload: Any,
        source: str,
        created_at: datetime,
        expires_at: datetime | None = None,
    ) -> ArtifactRef:
        return self.store.put_json(
            task_id,
            kind=kind,
            payload=payload,
            source=source,
            created_at=created_at,
            expires_at=expires_at,
        )

    def save_bytes(
        self,
        task_id: str,
        *,
        kind: ArtifactKind,
        payload: bytes,
        media_type: str,
        source: str,
        created_at: datetime,
        expires_at: datetime | None = None,
    ) -> ArtifactRef:
        return self.store.put_bytes(
            task_id,
            kind=kind,
            payload=payload,
            media_type=media_type,
            source=source,
            created_at=created_at,
            expires_at=expires_at,
        )

    def open_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes:
        return self.store.open_bytes(task_id, artifact)

    def load_json(self, task_id: str, artifact: ArtifactRef) -> Any:
        return self.store.load_json(task_id, artifact)

    def cleanup_expired(self, *, now: datetime) -> ArtifactCleanupResult:
        return self.store.cleanup_expired(now=now)


__all__ = ["ArtifactService"]
