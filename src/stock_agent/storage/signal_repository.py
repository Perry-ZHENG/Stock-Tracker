"""Persistence for the V2 signal discovery, validation, and approval lifecycle."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from stock_agent.contracts.signals import (
    CandidateFunction,
    ExistingSignal,
    SignalApproval,
    SignalObservation,
    SignalValidationResult,
    SignalVersion,
)
from stock_agent.security.redaction import redact_sensitive


class SignalRepository:
    """Persist only validated lifecycle state; code execution remains outside this repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def upsert_definition(
        self,
        definition: ExistingSignal,
        *,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO signal_definitions (
                signal_id, name, feature_fingerprint, status, current_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                name = excluded.name,
                feature_fingerprint = excluded.feature_fingerprint,
                status = excluded.status,
                current_version = excluded.current_version,
                updated_at = excluded.updated_at
            """,
            (
                definition.signal_id,
                definition.name,
                definition.feature_fingerprint,
                definition.status,
                definition.version,
                _timestamp(created_at),
                _timestamp(updated_at),
            ),
        )
        self.connection.commit()

    def get_definition(self, signal_id: str) -> ExistingSignal | None:
        row = self.connection.execute(
            "SELECT * FROM signal_definitions WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            return None
        return ExistingSignal(
            signal_id=row["signal_id"],
            version=row["current_version"],
            name=row["name"],
            feature_fingerprint=row["feature_fingerprint"],
            status=row["status"],
        )

    def save_candidate(self, candidate: CandidateFunction, *, created_at: datetime) -> None:
        self.connection.execute(
            """
            INSERT INTO candidate_functions (
                candidate_id, proposal_id, interface_version, source_artifact_id, source_hash,
                dependencies_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.candidate_id,
                candidate.proposal_id,
                candidate.interface_version,
                candidate.source_artifact.artifact_id,
                candidate.source_hash,
                _json(candidate.dependencies),
                _timestamp(created_at),
            ),
        )
        self.connection.commit()

    def get_candidate(self, candidate_id: str) -> CandidateFunction | None:
        row = self.connection.execute(
            """
            SELECT candidate_functions.*, artifacts.kind, artifacts.sha256, artifacts.media_type,
                   artifacts.size_bytes, artifacts.created_at AS artifact_created_at,
                   artifacts.expires_at AS artifact_expires_at
            FROM candidate_functions
            JOIN artifacts ON artifacts.artifact_id = candidate_functions.source_artifact_id
            WHERE candidate_functions.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return CandidateFunction(
            candidate_id=row["candidate_id"],
            proposal_id=row["proposal_id"],
            interface_version=row["interface_version"],
            source_artifact={
                "artifact_id": row["source_artifact_id"],
                "kind": row["kind"],
                "sha256": row["sha256"],
                "media_type": row["media_type"],
                "size_bytes": row["size_bytes"],
                "created_at": row["artifact_created_at"],
                "expires_at": row["artifact_expires_at"],
            },
            source_hash=row["source_hash"],
            dependencies=json.loads(row["dependencies_json"]),
        )

    def save_validation(self, result: SignalValidationResult, *, created_at: datetime) -> None:
        self.connection.execute(
            """
            INSERT INTO signal_validations (validation_id, candidate_id, payload_json, decision, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                result.validation_id,
                result.candidate_id,
                _json(result.model_dump(mode="json")),
                result.decision,
                _timestamp(created_at),
            ),
        )
        self.connection.commit()

    def get_validation(self, validation_id: str) -> SignalValidationResult | None:
        row = self.connection.execute(
            "SELECT payload_json FROM signal_validations WHERE validation_id = ?", (validation_id,)
        ).fetchone()
        return SignalValidationResult.model_validate_json(row["payload_json"]) if row is not None else None

    def save_version(self, version: SignalVersion) -> None:
        payload = version.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO signal_versions (signal_id, version, status, source_hash, validation_id, approved_by, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.signal_id,
                version.version,
                version.status,
                version.source_hash,
                version.validation_id,
                version.approved_by,
                payload["approved_at"],
            ),
        )
        self.connection.commit()

    def get_version(self, signal_id: str, version: int) -> SignalVersion | None:
        row = self.connection.execute(
            "SELECT * FROM signal_versions WHERE signal_id = ? AND version = ?", (signal_id, version)
        ).fetchone()
        if row is None:
            return None
        return SignalVersion(
            signal_id=row["signal_id"],
            version=row["version"],
            status=row["status"],
            source_hash=row["source_hash"],
            validation_id=row["validation_id"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
        )

    def append_observation(self, observation: SignalObservation) -> None:
        payload = observation.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO signal_observations (
                signal_id, version, symbol, timestamp, label, strength, confidence, reason, evidence_refs_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.signal_id,
                observation.version,
                observation.symbol,
                payload["timestamp"],
                observation.label,
                observation.strength,
                observation.confidence,
                observation.reason,
                _json(payload["evidence_refs"]),
            ),
        )
        self.connection.commit()

    def list_observations(self, signal_id: str, version: int) -> list[SignalObservation]:
        rows = self.connection.execute(
            """
            SELECT * FROM signal_observations
            WHERE signal_id = ? AND version = ?
            ORDER BY timestamp, observation_id
            """,
            (signal_id, version),
        ).fetchall()
        return [
            SignalObservation(
                signal_id=row["signal_id"],
                version=row["version"],
                symbol=row["symbol"],
                timestamp=row["timestamp"],
                label=row["label"],
                strength=row["strength"],
                confidence=row["confidence"],
                reason=row["reason"],
                evidence_refs=json.loads(row["evidence_refs_json"]),
            )
            for row in rows
        ]

    def record_approval(self, approval: SignalApproval) -> None:
        payload = approval.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO approvals (
                approval_id, subject_type, subject_id, decision, decided_by, reason, decided_at
            ) VALUES (?, 'signal_version', ?, ?, ?, ?, ?)
            """,
            (
                approval.approval_id,
                f"{approval.signal_id}:{approval.version}",
                approval.decision,
                approval.decided_by,
                approval.reason,
                payload["decided_at"],
            ),
        )
        self.connection.commit()


def _json(value: object) -> str:
    return json.dumps(redact_sensitive(value), ensure_ascii=False, sort_keys=True)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("repository timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["SignalRepository"]
