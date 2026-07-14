"""Content-addressed Artifact payload storage with task isolation and hash verification."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stock_agent.contracts.evidence import ArtifactKind, ArtifactRef
from stock_agent.security.redaction import redact_sensitive, redact_text
from stock_agent.storage.retention import list_expired_artifacts
from stock_agent.storage.task_repository import RepositoryStateError, StoredArtifact, TaskRepository

DEFAULT_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
DEFAULT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/x-python",
        "application/x-python-code",
    }
)


class ArtifactStoreError(RuntimeError):
    """Base error for rejected, missing, expired, or corrupted Artifact data."""


class ArtifactAccessError(ArtifactStoreError):
    """Raised when a caller attempts to access an Artifact outside its task boundary."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when persisted bytes no longer match their declared SHA-256 digest."""


class ArtifactValidationError(ArtifactStoreError):
    """Raised when Artifact metadata or payload violates storage policy."""


@dataclass(frozen=True)
class ArtifactCleanupResult:
    artifact_count: int
    evidence_count: int
    payload_count: int


class ArtifactStore:
    """Store redacted bytes below a fixed root; no caller-supplied path is ever read."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        parquet_root: Path,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        allowed_media_types: frozenset[str] = DEFAULT_MEDIA_TYPES,
    ) -> None:
        if max_artifact_bytes <= 0:
            raise ValueError("max_artifact_bytes must be positive")
        self.connection = connection
        self.repository = TaskRepository(connection)
        self.parquet_root = parquet_root.resolve()
        self.payload_root = self.parquet_root / "artifacts" / "sha256"
        self.max_artifact_bytes = max_artifact_bytes
        self.allowed_media_types = allowed_media_types

    def put_json(
        self,
        task_id: str,
        *,
        kind: ArtifactKind,
        payload: Any,
        source: str,
        created_at: datetime,
        expires_at: datetime | None = None,
    ) -> ArtifactRef:
        serialized = json.dumps(redact_sensitive(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.put_bytes(
            task_id,
            kind=kind,
            payload=serialized,
            media_type="application/json",
            source=source,
            created_at=created_at,
            expires_at=expires_at,
        )

    def put_bytes(
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
        if not task_id or not source:
            raise ArtifactValidationError("task_id and source must be non-empty")
        if self.repository.get_task(task_id) is None:
            raise ArtifactAccessError("artifact task does not exist")
        if media_type not in self.allowed_media_types:
            raise ArtifactValidationError(f"media_type {media_type!r} is not allowed")
        if created_at.tzinfo is None or (expires_at is not None and expires_at.tzinfo is None):
            raise ArtifactValidationError("artifact timestamps must be timezone-aware")

        sanitized = _sanitize_payload(payload, media_type)
        if len(sanitized) > self.max_artifact_bytes:
            raise ArtifactValidationError("artifact payload exceeds the configured size limit")
        sha256 = hashlib.sha256(sanitized).hexdigest()
        existing = self.repository.find_artifact_by_hash(task_id, sha256)
        if existing is not None:
            if existing.source != source:
                raise ArtifactValidationError("identical task payloads must not be rebound to a different source")
            if existing.ref.kind != kind or existing.ref.media_type != media_type:
                raise ArtifactValidationError("identical task payloads must keep their original kind and media type")
            return existing.ref

        artifact = ArtifactRef(
            artifact_id=_artifact_id(task_id, sha256),
            kind=kind,
            sha256=sha256,
            media_type=media_type,
            size_bytes=len(sanitized),
            created_at=created_at,
            expires_at=expires_at,
        )
        storage_key = self._storage_key(sha256)
        self._write_atomically(storage_key, sanitized, expected_sha256=sha256)
        try:
            self.repository.register_artifact(task_id, artifact, storage_key=storage_key, source=source)
        except sqlite3.IntegrityError as exc:
            duplicate = self.repository.find_artifact_by_hash(task_id, sha256)
            if duplicate is not None:
                return duplicate.ref
            raise ArtifactStoreError("artifact metadata could not be persisted") from exc
        return artifact

    def get_metadata(
        self,
        task_id: str,
        artifact: ArtifactRef,
        *,
        now: datetime | None = None,
    ) -> StoredArtifact:
        stored = self.repository.get_artifact(task_id, artifact.artifact_id)
        if stored is None or stored.ref != artifact:
            raise ArtifactAccessError("artifact is unavailable for this task")
        self._ensure_not_expired(stored.ref, now=now)
        return stored

    def open_bytes(self, task_id: str, artifact: ArtifactRef) -> bytes:
        stored = self.get_metadata(task_id, artifact)
        path = self._path_for_key(stored.storage_key)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactAccessError("artifact payload is missing") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != stored.ref.sha256:
            raise ArtifactIntegrityError("artifact payload hash does not match metadata")
        return payload

    def load_json(self, task_id: str, artifact: ArtifactRef) -> Any:
        if artifact.media_type != "application/json":
            raise ArtifactValidationError("load_json requires an application/json artifact")
        try:
            return json.loads(self.open_bytes(task_id, artifact).decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ArtifactIntegrityError("JSON artifact is not valid UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError("JSON artifact is not valid JSON") from exc

    def cleanup_expired(self, *, now: datetime) -> ArtifactCleanupResult:
        """Remove expired metadata, dependent evidence, and unreferenced payload files."""

        if now.tzinfo is None:
            raise ArtifactValidationError("cleanup time must be timezone-aware")
        expired = list_expired_artifacts(self.connection, now=now)
        if not expired:
            return ArtifactCleanupResult(artifact_count=0, evidence_count=0, payload_count=0)

        artifact_ids = [record.artifact_id for record in expired]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _artifact_id in artifact_ids)
            evidence_count = self.connection.execute(
                f"DELETE FROM evidence WHERE artifact_id IN ({placeholders})", artifact_ids
            ).rowcount
            artifact_count = self.connection.execute(
                f"DELETE FROM artifacts WHERE artifact_id IN ({placeholders})", artifact_ids
            ).rowcount
            retained_keys = {
                row["storage_key"]
                for row in self.connection.execute(
                    f"SELECT DISTINCT storage_key FROM artifacts WHERE storage_key IN ({placeholders})",
                    [record.storage_key for record in expired],
                ).fetchall()
            }
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

        payload_count = 0
        for record in expired:
            if record.storage_key in retained_keys:
                continue
            path = self._path_for_key(record.storage_key)
            try:
                path.unlink()
                payload_count += 1
            except FileNotFoundError:
                continue
        return ArtifactCleanupResult(
            artifact_count=artifact_count,
            evidence_count=evidence_count,
            payload_count=payload_count,
        )

    def _storage_key(self, sha256: str) -> str:
        return f"artifacts/sha256/{sha256[:2]}/{sha256}"

    def _write_atomically(self, storage_key: str, payload: bytes, *, expected_sha256: str) -> None:
        path = self._path_for_key(storage_key)
        if path.exists():
            existing_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if existing_hash != expected_sha256:
                raise ArtifactIntegrityError("existing content-addressed payload has an unexpected hash")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            if hashlib.sha256(Path(temporary_name).read_bytes()).hexdigest() != expected_sha256:
                raise ArtifactIntegrityError("temporary artifact hash verification failed")
            os.replace(temporary_name, path)
        finally:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()

    def _path_for_key(self, storage_key: str) -> Path:
        candidate = Path(storage_key)
        if candidate.is_absolute():
            raise ArtifactAccessError("artifact storage keys must be relative")
        path = (self.parquet_root / candidate).resolve()
        if not path.is_relative_to(self.parquet_root):
            raise ArtifactAccessError("artifact storage key escapes the configured root")
        return path

    @staticmethod
    def _ensure_not_expired(artifact: ArtifactRef, *, now: datetime | None = None) -> None:
        active_now = now or datetime.now(UTC)
        if active_now.tzinfo is None:
            raise ArtifactValidationError("validation time must be timezone-aware")
        if artifact.expires_at is not None and artifact.expires_at <= active_now:
            raise ArtifactAccessError("artifact has expired")


def _sanitize_payload(payload: bytes, media_type: str) -> bytes:
    if media_type == "application/json":
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("application/json payload must be valid UTF-8 JSON") from exc
        return json.dumps(redact_sensitive(decoded), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactValidationError("allowed artifact media types must contain UTF-8 text") from exc
    return (redact_text(text) or "").encode("utf-8")


def _artifact_id(task_id: str, sha256: str) -> str:
    task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return f"artifact-{task_digest}-{sha256[:20]}"


__all__ = [
    "ArtifactAccessError",
    "ArtifactCleanupResult",
    "ArtifactIntegrityError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ArtifactValidationError",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MEDIA_TYPES",
]
