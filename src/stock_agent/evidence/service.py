"""Task-scoped Evidence creation and validation for stored Artifacts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from stock_agent.artifacts.store import ArtifactAccessError, ArtifactStore
from stock_agent.contracts.evidence import ArtifactRef, EvidenceBundle, EvidenceRef, EvidenceType, TrustLevel
from stock_agent.storage.task_repository import RepositoryStateError, TaskRepository


class EvidenceServiceError(RuntimeError):
    """Raised when Evidence cannot be tied to a valid artifact in the same task."""


class EvidenceService:
    """Create references only after ownership, source, and expiry checks pass."""

    def __init__(self, connection: sqlite3.Connection, artifact_store: ArtifactStore) -> None:
        self.connection = connection
        self.artifact_store = artifact_store
        self.repository = TaskRepository(connection)

    def create(
        self,
        task_id: str,
        *,
        artifact: ArtifactRef,
        evidence_type: EvidenceType,
        source: str,
        observed_at: datetime,
        trust_level: TrustLevel = "medium",
        valid_until: datetime | None = None,
        evidence_id: str | None = None,
    ) -> EvidenceRef:
        if observed_at.tzinfo is None or (valid_until is not None and valid_until.tzinfo is None):
            raise EvidenceServiceError("evidence timestamps must be timezone-aware")
        try:
            stored = self.artifact_store.get_metadata(task_id, artifact)
        except ArtifactAccessError as exc:
            raise EvidenceServiceError("evidence artifact is missing, expired, or belongs to another task") from exc
        if stored.source != source:
            raise EvidenceServiceError("evidence source must match the stored artifact source")
        if artifact.expires_at is not None and observed_at > artifact.expires_at:
            raise EvidenceServiceError("evidence cannot be observed after its artifact expires")
        if artifact.expires_at is not None and valid_until is not None and valid_until > artifact.expires_at:
            raise EvidenceServiceError("evidence validity cannot outlive its artifact")
        evidence = EvidenceRef(
            evidence_id=evidence_id or f"evidence-{uuid4().hex}",
            evidence_type=evidence_type,
            artifact_id=artifact.artifact_id,
            source=source,
            observed_at=observed_at,
            valid_until=valid_until,
            trust_level=trust_level,
        )
        try:
            self.repository.register_evidence(task_id, evidence)
        except (RepositoryStateError, sqlite3.IntegrityError) as exc:
            raise EvidenceServiceError("evidence metadata could not be persisted") from exc
        return evidence

    def get(self, task_id: str, evidence_id: str, *, now: datetime | None = None) -> EvidenceRef:
        evidence = self.repository.get_evidence(task_id, evidence_id)
        if evidence is None:
            raise EvidenceServiceError("evidence is unavailable for this task")
        active_now = now or datetime.now(UTC)
        if active_now.tzinfo is None:
            raise EvidenceServiceError("validation time must be timezone-aware")
        if evidence.valid_until is not None and evidence.valid_until < active_now:
            raise EvidenceServiceError("evidence has expired")
        artifact = self._artifact_for_evidence(task_id, evidence)
        try:
            self.artifact_store.get_metadata(task_id, artifact, now=active_now)
        except ArtifactAccessError as exc:
            raise EvidenceServiceError("evidence artifact is missing or expired") from exc
        return evidence

    def build_bundle(self, task_id: str, evidence_refs: list[EvidenceRef], *, now: datetime | None = None) -> EvidenceBundle:
        artifacts: dict[str, ArtifactRef] = {}
        for reference in evidence_refs:
            stored = self.get(task_id, reference.evidence_id, now=now)
            if stored != reference:
                raise EvidenceServiceError("evidence reference does not match persisted metadata")
            artifacts[reference.artifact_id] = self._artifact_for_evidence(task_id, reference)
        return EvidenceBundle(task_id=task_id, artifact_refs=list(artifacts.values()), evidence_refs=evidence_refs)

    def _artifact_for_evidence(self, task_id: str, evidence: EvidenceRef) -> ArtifactRef:
        stored = self.repository.get_artifact(task_id, evidence.artifact_id)
        if stored is None:
            raise EvidenceServiceError("evidence references an unavailable artifact")
        if stored.source != evidence.source:
            raise EvidenceServiceError("evidence source no longer matches its artifact")
        return stored.ref


__all__ = ["EvidenceService", "EvidenceServiceError"]
