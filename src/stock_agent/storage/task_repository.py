"""Transactional persistence for V2 tasks, plans, steps, messages, and evidence metadata."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from stock_agent.contracts.evidence import ArtifactRef, EvidenceRef
from stock_agent.contracts.tasks import AgentMessage, AgentPlan, AgentStep, AgentTask
from stock_agent.security.redaction import redact_sensitive

TaskTransition = Literal["pending", "running", "paused", "cancelled", "failed", "completed"]
StepCompletionStatus = Literal["succeeded", "failed", "skipped", "cancelled"]

_TASK_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled", "failed"},
    "running": {"paused", "cancelled", "failed", "completed"},
    "paused": {"running", "cancelled"},
    "cancelled": set(),
    "failed": set(),
    "completed": set(),
}
_STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled"},
    "running": {"succeeded", "failed", "skipped", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "skipped": set(),
    "cancelled": set(),
}


class RepositoryStateError(RuntimeError):
    """Raised when a compare-and-set update sees a missing or invalid state."""


@dataclass(frozen=True)
class StoredArtifact:
    ref: ArtifactRef
    source: str
    storage_key: str


class TaskRepository:
    """Keep task mutations atomic so workers cannot claim the same step twice."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create_task(self, task: AgentTask) -> None:
        payload = task.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO agent_tasks (task_id, request_json, status, budget_json, created_at, execution_started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                _json(payload["request"]),
                task.status,
                _json(payload["budget"]),
                payload["created_at"],
                payload["execution_started_at"],
                payload["updated_at"],
            ),
        )
        self.connection.commit()

    def get_task(self, task_id: str) -> AgentTask | None:
        row = self.connection.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row is not None else None

    def list_tasks(self, *, statuses: tuple[TaskTransition, ...] | None = None) -> list[AgentTask]:
        """Return durable tasks for a worker without exposing raw request JSON."""

        if statuses is not None and not statuses:
            return []
        if statuses is None:
            rows = self.connection.execute("SELECT * FROM agent_tasks ORDER BY created_at, task_id").fetchall()
        else:
            placeholders = ",".join("?" for _status in statuses)
            rows = self.connection.execute(
                f"SELECT * FROM agent_tasks WHERE status IN ({placeholders}) ORDER BY created_at, task_id",
                statuses,
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def transition_task(
        self,
        task_id: str,
        *,
        expected_status: TaskTransition,
        new_status: TaskTransition,
        updated_at: datetime,
    ) -> AgentTask:
        if new_status not in _TASK_TRANSITIONS[expected_status]:
            raise RepositoryStateError(f"task transition {expected_status} -> {new_status} is not allowed")
        result = self.connection.execute(
            """
            UPDATE agent_tasks
            SET status = ?, updated_at = ?
            WHERE task_id = ? AND status = ?
            """,
            (new_status, _timestamp(updated_at), task_id, expected_status),
        )
        self.connection.commit()
        if result.rowcount != 1:
            raise RepositoryStateError(f"task {task_id} was not in expected state {expected_status}")
        task = self.get_task(task_id)
        if task is None:
            raise RepositoryStateError(f"task {task_id} disappeared after transition")
        return task

    def start_execution_if_needed(self, task_id: str, *, started_at: datetime) -> AgentTask:
        """Persist the budget clock exactly once when a running task is first worked."""

        timestamp = _timestamp(started_at)
        result = self.connection.execute(
            """
            UPDATE agent_tasks
            SET execution_started_at = COALESCE(execution_started_at, ?), updated_at = ?
            WHERE task_id = ? AND status = 'running'
            """,
            (timestamp, timestamp, task_id),
        )
        self.connection.commit()
        if result.rowcount != 1:
            raise RepositoryStateError(f"task {task_id} is not running")
        task = self.get_task(task_id)
        if task is None:
            raise RepositoryStateError(f"task {task_id} disappeared after starting execution")
        return task

    def save_plan(self, plan: AgentPlan, *, created_at: datetime | None = None) -> None:
        created = _timestamp(created_at or datetime.now(UTC))
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO agent_plans (plan_id, task_id, revision, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plan.plan_id, plan.task_id, plan.revision, plan.reason, created),
            )
            for step in plan.steps:
                self.connection.execute(
                    """
                    INSERT INTO agent_steps (
                        step_id, plan_id, task_id, actor, depends_on_json, input_refs_json,
                        status, attempt, max_attempts, claimed_by, claimed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        step.step_id,
                        plan.plan_id,
                        plan.task_id,
                        step.actor,
                        _json(step.depends_on),
                        _json(step.input_refs),
                        step.status,
                        step.attempt,
                        step.max_attempts,
                        created,
                    ),
                )
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def get_plan(self, plan_id: str) -> AgentPlan | None:
        plan_row = self.connection.execute("SELECT * FROM agent_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if plan_row is None:
            return None
        step_rows = self.connection.execute(
            "SELECT * FROM agent_steps WHERE plan_id = ? ORDER BY rowid", (plan_id,)
        ).fetchall()
        return AgentPlan(
            plan_id=plan_row["plan_id"],
            task_id=plan_row["task_id"],
            revision=plan_row["revision"],
            reason=plan_row["reason"],
            steps=[_step_from_row(row) for row in step_rows],
        )

    def get_latest_plan(self, task_id: str) -> AgentPlan | None:
        """Return the latest persisted plan revision for one orchestrated task."""

        row = self.connection.execute(
            """
            SELECT plan_id FROM agent_plans
            WHERE task_id = ?
            ORDER BY revision DESC, created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return self.get_plan(row["plan_id"]) if row is not None else None

    def list_steps(self, task_id: str, *, plan_id: str | None = None) -> list[AgentStep]:
        """Load task-scoped steps in creation order for restart-safe adapters."""

        if plan_id is None:
            rows = self.connection.execute(
                "SELECT * FROM agent_steps WHERE task_id = ? ORDER BY rowid", (task_id,)
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM agent_steps WHERE task_id = ? AND plan_id = ? ORDER BY rowid",
                (task_id, plan_id),
            ).fetchall()
        return [_step_from_row(row) for row in rows]

    def claim_next_step(
        self,
        task_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
    ) -> AgentStep | None:
        """Atomically claim one dependency-ready pending step for a worker."""

        if not worker_id:
            raise ValueError("worker_id must be non-empty")
        timestamp = _timestamp(claimed_at)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            rows = self.connection.execute(
                """
                SELECT * FROM agent_steps
                WHERE task_id = ? AND status = 'pending' AND attempt < max_attempts
                ORDER BY rowid
                """,
                (task_id,),
            ).fetchall()
            for row in rows:
                dependencies = json.loads(row["depends_on_json"])
                if not self._dependencies_satisfied(row["plan_id"], dependencies):
                    continue
                updated = self.connection.execute(
                    """
                    UPDATE agent_steps
                    SET status = 'running', attempt = attempt + 1, claimed_by = ?, claimed_at = ?, updated_at = ?
                    WHERE task_id = ? AND step_id = ? AND status = 'pending' AND attempt < max_attempts
                    """,
                    (worker_id, timestamp, timestamp, task_id, row["step_id"]),
                )
                if updated.rowcount == 1:
                    claimed_row = self.connection.execute(
                        "SELECT * FROM agent_steps WHERE task_id = ? AND step_id = ?",
                        (task_id, row["step_id"]),
                    ).fetchone()
                    self.connection.commit()
                    return _step_from_row(claimed_row)
            self.connection.commit()
            return None
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def complete_step(
        self,
        step_id: str,
        *,
        task_id: str | None = None,
        expected_status: Literal["pending", "running"],
        new_status: StepCompletionStatus,
        updated_at: datetime,
    ) -> AgentStep:
        if new_status not in _STEP_TRANSITIONS[expected_status]:
            raise RepositoryStateError(f"step transition {expected_status} -> {new_status} is not allowed")
        scoped_task_id = self._resolve_step_task_id(step_id, task_id)
        result = self.connection.execute(
            """
            UPDATE agent_steps
            SET status = ?, updated_at = ?
            WHERE task_id = ? AND step_id = ? AND status = ?
            """,
            (new_status, _timestamp(updated_at), scoped_task_id, step_id, expected_status),
        )
        self.connection.commit()
        if result.rowcount != 1:
            raise RepositoryStateError(f"step {step_id} was not in expected state {expected_status}")
        row = self.connection.execute(
            "SELECT * FROM agent_steps WHERE task_id = ? AND step_id = ?",
            (scoped_task_id, step_id),
        ).fetchone()
        if row is None:
            raise RepositoryStateError(f"step {step_id} disappeared after transition")
        return _step_from_row(row)

    def record_step_failure(
        self,
        step_id: str,
        *,
        task_id: str | None = None,
        updated_at: datetime,
    ) -> AgentStep:
        """Requeue a running step while attempts remain, otherwise finish it as failed."""

        scoped_task_id = self._resolve_step_task_id(step_id, task_id)
        row = self.connection.execute(
            "SELECT * FROM agent_steps WHERE task_id = ? AND step_id = ?",
            (scoped_task_id, step_id),
        ).fetchone()
        if row is None or row["status"] != "running":
            raise RepositoryStateError(f"step {step_id} was not running")
        next_status = "pending" if row["attempt"] < row["max_attempts"] else "failed"
        result = self.connection.execute(
            """
            UPDATE agent_steps
            SET status = ?, claimed_by = NULL, claimed_at = NULL, updated_at = ?
            WHERE task_id = ? AND step_id = ? AND status = 'running'
            """,
            (next_status, _timestamp(updated_at), scoped_task_id, step_id),
        )
        self.connection.commit()
        if result.rowcount != 1:
            raise RepositoryStateError(f"step {step_id} could not record failure")
        updated = self.connection.execute(
            "SELECT * FROM agent_steps WHERE task_id = ? AND step_id = ?",
            (scoped_task_id, step_id),
        ).fetchone()
        assert updated is not None
        return _step_from_row(updated)

    def claim_ready_steps(
        self,
        task_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        limit: int = 1,
    ) -> list[AgentStep]:
        """Claim up to ``limit`` dependency-ready steps only while the task is running."""

        if limit < 1:
            raise ValueError("limit must be positive")
        task = self.get_task(task_id)
        if task is None:
            raise RepositoryStateError(f"task {task_id} does not exist")
        if task.status != "running":
            return []
        claimed: list[AgentStep] = []
        for index in range(limit):
            step = self.claim_next_step(task_id, worker_id=f"{worker_id}:{index}", claimed_at=claimed_at)
            if step is None:
                break
            claimed.append(step)
        return claimed

    def recover_running_steps(self, task_id: str, *, updated_at: datetime) -> list[AgentStep]:
        """Recover interrupted steps after a process restart without resetting attempts."""

        timestamp = _timestamp(updated_at)
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            rows = self.connection.execute(
                "SELECT * FROM agent_steps WHERE task_id = ? AND status = 'running' ORDER BY rowid",
                (task_id,),
            ).fetchall()
            for row in rows:
                next_status = "pending" if row["attempt"] < row["max_attempts"] else "failed"
                self.connection.execute(
                    """
                    UPDATE agent_steps
                    SET status = ?, claimed_by = NULL, claimed_at = NULL, updated_at = ?
                    WHERE task_id = ? AND step_id = ?
                    """,
                    (next_status, timestamp, task_id, row["step_id"]),
                )
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise
        return [
            _step_from_row(row)
            for row in self.connection.execute(
                "SELECT * FROM agent_steps WHERE task_id = ? AND updated_at = ? ORDER BY rowid",
                (task_id, timestamp),
            ).fetchall()
        ]

    def cancel_open_steps(self, task_id: str, *, updated_at: datetime) -> int:
        """Cancel all pending or running steps after the task-level cancellation wins."""

        result = self.connection.execute(
            """
            UPDATE agent_steps
            SET status = 'cancelled', updated_at = ?
            WHERE task_id = ? AND status IN ('pending', 'running')
            """,
            (_timestamp(updated_at), task_id),
        )
        self.connection.commit()
        return result.rowcount

    def add_message(self, message: AgentMessage) -> None:
        payload = message.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO agent_messages (
                message_id, task_id, sender, recipient, summary, artifact_refs_json, evidence_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.task_id,
                message.sender,
                message.recipient,
                message.summary,
                _json(payload["artifact_refs"]),
                _json(payload["evidence_refs"]),
                payload["created_at"],
            ),
        )
        self.connection.commit()

    def list_messages(self, task_id: str) -> list[AgentMessage]:
        rows = self.connection.execute(
            "SELECT * FROM agent_messages WHERE task_id = ? ORDER BY created_at, rowid", (task_id,)
        ).fetchall()
        return [
            AgentMessage(
                message_id=row["message_id"],
                task_id=row["task_id"],
                sender=row["sender"],
                recipient=row["recipient"],
                summary=row["summary"],
                artifact_refs=json.loads(row["artifact_refs_json"]),
                evidence_refs=json.loads(row["evidence_refs_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_step_input(self, task_id: str, step_id: str, payload: object, *, updated_at: datetime) -> None:
        """Persist the typed input needed to resume one runtime step after restart."""

        row = self.connection.execute(
            "SELECT task_id FROM agent_steps WHERE task_id = ? AND step_id = ?", (task_id, step_id)
        ).fetchone()
        if row is None:
            raise RepositoryStateError("step does not belong to the supplied task")
        self.connection.execute(
            """
            INSERT INTO agent_step_payloads (step_id, task_id, input_json, output_artifact_id, updated_at)
            VALUES (?, ?, ?, NULL, ?)
            ON CONFLICT(task_id, step_id) DO UPDATE SET input_json = excluded.input_json, updated_at = excluded.updated_at
            """,
            (step_id, task_id, _json(payload), _timestamp(updated_at)),
        )
        self.connection.commit()

    def get_step_input(self, task_id: str, step_id: str) -> object | None:
        row = self.connection.execute(
            "SELECT input_json FROM agent_step_payloads WHERE task_id = ? AND step_id = ?", (task_id, step_id)
        ).fetchone()
        return json.loads(row["input_json"]) if row is not None else None

    def record_step_output(self, task_id: str, step_id: str, *, artifact_id: str, updated_at: datetime) -> None:
        row = self.connection.execute(
            "SELECT task_id FROM agent_step_payloads WHERE task_id = ? AND step_id = ?", (task_id, step_id)
        ).fetchone()
        if row is None:
            raise RepositoryStateError("a step output requires a persisted input for the same task")
        self.connection.execute(
            """
            UPDATE agent_step_payloads
            SET output_artifact_id = ?, updated_at = ?
            WHERE step_id = ? AND task_id = ?
            """,
            (artifact_id, _timestamp(updated_at), step_id, task_id),
        )
        self.connection.commit()

    def get_step_output_artifact_id(self, task_id: str, step_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT output_artifact_id FROM agent_step_payloads WHERE task_id = ? AND step_id = ?",
            (task_id, step_id),
        ).fetchone()
        return str(row["output_artifact_id"]) if row is not None and row["output_artifact_id"] is not None else None

    def _resolve_step_task_id(self, step_id: str, task_id: str | None) -> str:
        """Require a task scope once more than one task can share a step name."""

        if task_id is not None:
            return task_id
        rows = self.connection.execute(
            "SELECT task_id FROM agent_steps WHERE step_id = ? ORDER BY rowid",
            (step_id,),
        ).fetchall()
        if len(rows) != 1:
            raise RepositoryStateError(f"task_id is required for non-unique step {step_id}")
        return str(rows[0]["task_id"])

    def register_artifact(
        self,
        task_id: str,
        artifact: ArtifactRef,
        *,
        storage_key: str,
        source: str = "unknown",
    ) -> None:
        """Persist Artifact metadata; V2-103 owns the byte-level store behind ``storage_key``."""

        if not storage_key or not source:
            raise ValueError("storage_key and source must be non-empty")
        payload = artifact.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, task_id, kind, sha256, media_type, size_bytes, storage_key, source, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                task_id,
                artifact.kind,
                artifact.sha256,
                artifact.media_type,
                artifact.size_bytes,
                storage_key,
                source,
                payload["created_at"],
                payload["expires_at"],
            ),
        )
        self.connection.commit()

    def get_artifact(self, task_id: str, artifact_id: str) -> StoredArtifact | None:
        row = self.connection.execute(
            """
            SELECT artifact_id, kind, sha256, media_type, size_bytes, created_at, expires_at, source, storage_key
            FROM artifacts
            WHERE task_id = ? AND artifact_id = ?
            """,
            (task_id, artifact_id),
        ).fetchone()
        return _stored_artifact_from_row(row) if row is not None else None

    def find_artifact_by_hash(self, task_id: str, sha256: str) -> StoredArtifact | None:
        row = self.connection.execute(
            """
            SELECT artifact_id, kind, sha256, media_type, size_bytes, created_at, expires_at, source, storage_key
            FROM artifacts
            WHERE task_id = ? AND sha256 = ?
            """,
            (task_id, sha256),
        ).fetchone()
        return _stored_artifact_from_row(row) if row is not None else None

    def register_evidence(self, task_id: str, evidence: EvidenceRef) -> None:
        artifact = self.connection.execute(
            "SELECT task_id FROM artifacts WHERE artifact_id = ?", (evidence.artifact_id,)
        ).fetchone()
        if artifact is None or artifact["task_id"] != task_id:
            raise RepositoryStateError("evidence must reference an artifact owned by the same task")
        payload = evidence.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO evidence (
                evidence_id, task_id, artifact_id, evidence_type, source, observed_at, valid_until, trust_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.evidence_id,
                task_id,
                evidence.artifact_id,
                evidence.evidence_type,
                evidence.source,
                payload["observed_at"],
                payload["valid_until"],
                evidence.trust_level,
            ),
        )
        self.connection.commit()

    def get_evidence(self, task_id: str, evidence_id: str) -> EvidenceRef | None:
        row = self.connection.execute(
            """
            SELECT evidence_id, evidence_type, artifact_id, source, observed_at, valid_until, trust_level
            FROM evidence
            WHERE task_id = ? AND evidence_id = ?
            """,
            (task_id, evidence_id),
        ).fetchone()
        if row is None:
            return None
        return EvidenceRef(
            evidence_id=row["evidence_id"],
            evidence_type=row["evidence_type"],
            artifact_id=row["artifact_id"],
            source=row["source"],
            observed_at=row["observed_at"],
            valid_until=row["valid_until"],
            trust_level=row["trust_level"],
        )

    def _dependencies_satisfied(self, plan_id: str, dependencies: list[str]) -> bool:
        if not dependencies:
            return True
        placeholders = ",".join("?" for _dependency in dependencies)
        rows = self.connection.execute(
            f"SELECT step_id, status FROM agent_steps WHERE plan_id = ? AND step_id IN ({placeholders})",
            (plan_id, *dependencies),
        ).fetchall()
        statuses = {row["step_id"]: row["status"] for row in rows}
        return len(statuses) == len(dependencies) and all(
            statuses[dependency] in {"succeeded", "skipped"} for dependency in dependencies
        )


def _task_from_row(row: sqlite3.Row) -> AgentTask:
    return AgentTask(
        task_id=row["task_id"],
        request=json.loads(row["request_json"]),
        status=row["status"],
        budget=json.loads(row["budget_json"]),
        created_at=row["created_at"],
        execution_started_at=row["execution_started_at"],
        updated_at=row["updated_at"],
    )


def _step_from_row(row: sqlite3.Row) -> AgentStep:
    return AgentStep(
        step_id=row["step_id"],
        actor=row["actor"],
        depends_on=json.loads(row["depends_on_json"]),
        input_refs=json.loads(row["input_refs_json"]),
        status=row["status"],
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
    )


def _stored_artifact_from_row(row: sqlite3.Row) -> StoredArtifact:
    return StoredArtifact(
        ref=ArtifactRef(
            artifact_id=row["artifact_id"],
            kind=row["kind"],
            sha256=row["sha256"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        ),
        source=row["source"],
        storage_key=row["storage_key"],
    )


def _json(value: object) -> str:
    return json.dumps(redact_sensitive(value), ensure_ascii=False, sort_keys=True)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("repository timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["RepositoryStateError", "StoredArtifact", "TaskRepository"]
