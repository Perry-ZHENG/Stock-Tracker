"""Immutable signal-version registry; only validated code can become active."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.signals import CandidateFunction, ExistingSignal, SignalValidationResult, SignalVersion
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.signal_lab.feature_catalog import proposal_feature_names
from stock_agent.storage.signal_repository import SignalRepository


class SignalRegistryError(RuntimeError):
    """Raised when lifecycle state, hashes, or approval prerequisites are invalid."""


class SignalRegistry:
    """Create immutable validated versions and atomically maintain one active version."""

    def __init__(
        self,
        *,
        repository: SignalRepository,
        artifact_service: ArtifactService,
        safety_policy: ResearchSafetyPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.artifact_service = artifact_service
        self.safety_policy = safety_policy or ResearchSafetyPolicy(repository.connection)

    def register_validated(
        self,
        *,
        signal_id: str,
        name: str,
        feature_fingerprint: str,
        candidate: CandidateFunction,
        validation: SignalValidationResult,
        now: datetime,
    ) -> SignalVersion:
        self._authorize_research(name, capability="write_signal_candidate")
        if validation.decision != "pass" or validation.candidate_id != candidate.candidate_id:
            raise SignalRegistryError("only a passing validation for the same Candidate can enter the registry")
        persisted_candidate = self.repository.get_candidate(candidate.candidate_id)
        persisted_validation = self.repository.get_validation(validation.validation_id)
        provenance = self.repository.get_build_provenance(candidate.candidate_id)
        if persisted_candidate != candidate or persisted_validation != validation or provenance is None:
            raise SignalRegistryError("Candidate, validation, or provenance is not persisted")
        if validation.metrics_artifact is None:
            raise SignalRegistryError("passing validation must include a metrics Artifact")
        try:
            self.artifact_service.open_bytes(provenance.task_id, candidate.source_artifact)
            self.artifact_service.open_bytes(provenance.task_id, validation.metrics_artifact)
            proposal_feature_names(provenance.feature_catalog, [feature.name for feature in provenance.proposal.features])
        except Exception as exc:
            raise SignalRegistryError("Candidate provenance or validation Artifact is unavailable") from exc
        existing = self.repository.get_definition(signal_id)
        version_number = max((item.version for item in self.repository.list_versions(signal_id)), default=0) + 1
        definition = ExistingSignal(
            signal_id=signal_id,
            version=version_number,
            name=name,
            feature_fingerprint=feature_fingerprint,
            status="validated",
        )
        version = SignalVersion(
            signal_id=signal_id,
            version=version_number,
            status="validated",
            source_hash=candidate.source_hash,
            validation_id=validation.validation_id,
        )
        if existing is not None and (existing.name != name or existing.feature_fingerprint != feature_fingerprint):
            raise SignalRegistryError("a signal id cannot change its immutable name or feature fingerprint")
        self.repository.upsert_definition(definition, created_at=now, updated_at=now)
        self.repository.save_version(version)
        return version

    def activate(
        self,
        *,
        signal_id: str,
        version: int,
        approved_by: str,
        now: datetime,
        actor_type: str = "human_admin",
    ) -> SignalVersion:
        decision = self.safety_policy.inspect(
            SafetyRequest(
                source="signal_registry",
                actor_ref=approved_by,
                actor_type=actor_type,  # type: ignore[arg-type]
                requested_capability="approve_signal",
            )
        )
        if not decision.allowed:
            raise SignalRegistryError(f"signal activation is blocked: {decision.reason_code}")
        target = self.repository.get_version(signal_id, version)
        if target is None or target.status not in {"validated", "suspended"}:
            raise SignalRegistryError("only validated or suspended versions can be activated")
        validation = self.repository.get_validation(target.validation_id)
        candidate = self.repository.get_candidate_by_source_hash(target.source_hash)
        if validation is None or validation.decision != "pass" or candidate is None or candidate.source_hash != target.source_hash:
            raise SignalRegistryError("version hash or validation prerequisite no longer matches")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "UPDATE signal_versions SET status = 'suspended' WHERE signal_id = ? AND status = 'active'",
                (signal_id,),
            )
            updated = self.connection.execute(
                """
                UPDATE signal_versions
                SET status = 'active', approved_by = ?, approved_at = ?
                WHERE signal_id = ? AND version = ? AND status IN ('validated', 'suspended')
                """,
                (approved_by, _timestamp(now), signal_id, version),
            )
            if updated.rowcount != 1:
                raise SignalRegistryError("target version changed before activation")
            self.connection.execute(
                "UPDATE signal_definitions SET status = 'active', current_version = ?, updated_at = ? WHERE signal_id = ?",
                (version, _timestamp(now), signal_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        active = self.repository.get_version(signal_id, version)
        assert active is not None
        return active

    def suspend(self, *, signal_id: str, version: int, now: datetime) -> SignalVersion:
        target = self.repository.get_version(signal_id, version)
        if target is None or target.status != "active":
            raise SignalRegistryError("only an active version can be suspended")
        self.connection.execute(
            "UPDATE signal_versions SET status = 'suspended' WHERE signal_id = ? AND version = ? AND status = 'active'",
            (signal_id, version),
        )
        self.connection.execute(
            "UPDATE signal_definitions SET status = 'suspended', updated_at = ? WHERE signal_id = ?",
            (_timestamp(now), signal_id),
        )
        self.connection.commit()
        result = self.repository.get_version(signal_id, version)
        assert result is not None
        return result

    def rollback(
        self,
        *,
        signal_id: str,
        target_version: int,
        approved_by: str,
        now: datetime,
        actor_type: str = "human_admin",
    ) -> SignalVersion:
        return self.activate(
            signal_id=signal_id,
            version=target_version,
            approved_by=approved_by,
            now=now,
            actor_type=actor_type,
        )

    def list(self) -> list[ExistingSignal]:
        rows = self.connection.execute("SELECT * FROM signal_definitions ORDER BY signal_id").fetchall()
        return [
            ExistingSignal(
                signal_id=row["signal_id"],
                version=row["current_version"],
                name=row["name"],
                feature_fingerprint=row["feature_fingerprint"],
                status=row["status"],
            )
            for row in rows
        ]

    def show(self, signal_id: str) -> tuple[ExistingSignal, list[SignalVersion]]:
        definition = self.repository.get_definition(signal_id)
        if definition is None:
            raise SignalRegistryError("signal is not registered")
        return definition, self.repository.list_versions(signal_id)

    def diff(self, signal_id: str, left_version: int, right_version: int) -> dict[str, object]:
        left = self.repository.get_version(signal_id, left_version)
        right = self.repository.get_version(signal_id, right_version)
        if left is None or right is None:
            raise SignalRegistryError("both versions must exist for diff")
        return {
            "signal_id": signal_id,
            "left_version": left_version,
            "right_version": right_version,
            "source_hash_changed": left.source_hash != right.source_hash,
            "validation_changed": left.validation_id != right.validation_id,
            "status_changed": left.status != right.status,
        }

    def _authorize_research(self, raw_text: str, *, capability: str) -> None:
        decision = self.safety_policy.inspect(
            SafetyRequest(
                source="signal_registry",
                actor_type="system",
                requested_capability=capability,  # type: ignore[arg-type]
                raw_text=raw_text,
            )
        )
        if not decision.allowed:
            raise SignalRegistryError(f"signal registry action is blocked: {decision.reason_code}")

    @property
    def connection(self) -> sqlite3.Connection:
        return self.repository.connection


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise SignalRegistryError("registry timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["SignalRegistry", "SignalRegistryError"]
