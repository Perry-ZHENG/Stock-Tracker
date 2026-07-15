"""Process-isolated Candidate execution with static policy, time, memory, and output limits."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import ArtifactRef
from stock_agent.contracts.signals import CandidateFunction
from stock_agent.evidence.service import EvidenceService
from stock_agent.observability import AgentTrace, AgentTraceRecorder, BudgetLedger
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.signal_lab.ast_policy import AstPolicyError, validate_candidate_source
from stock_agent.signal_lab.feature_catalog import proposal_feature_names
from stock_agent.signal_lab.interface import SignalContext
from stock_agent.storage.signal_repository import SignalRepository


class SandboxPolicy(StrictSchema):
    version: str = Field(default="sandbox-v1", min_length=1)
    timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    memory_limit_mb: int = Field(default=64, ge=16, le=1_024)
    max_output_bytes: int = Field(default=64 * 1024, ge=1_024, le=1_048_576)
    max_points: int = Field(default=10_000, ge=1, le=100_000)


class SandboxRunResult(StrictSchema):
    candidate_id: str = Field(min_length=1)
    status: Literal["succeeded", "rejected", "failed", "timed_out", "resource_limited"]
    policy_version: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    exit_code: int | None = None
    point_artifact: ArtifactRef | None = None
    point_count: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def _validate_output(self) -> "SandboxRunResult":
        if self.status == "succeeded" and self.point_artifact is None:
            raise ValueError("a successful Sandbox run requires a SignalPoint artifact")
        if self.status != "succeeded" and self.point_artifact is not None:
            raise ValueError("a failed Sandbox run cannot expose a SignalPoint artifact")
        return self


class CandidateSandbox:
    """Never execute model source in the caller process or with inherited credentials."""

    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        repository: SignalRepository | None = None,
        policy: SandboxPolicy | None = None,
        safety_policy: ResearchSafetyPolicy | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.repository = repository or SignalRepository(artifact_service.store.connection)
        self.policy = policy or SandboxPolicy()
        connection = artifact_service.store.connection
        self.budget_ledger = BudgetLedger(connection)
        self.trace_recorder = AgentTraceRecorder(connection)
        self.safety_policy = safety_policy or ResearchSafetyPolicy(connection)

    def run(
        self,
        task_id: str,
        candidate: CandidateFunction,
        context_artifact: ArtifactRef,
        *,
        now: datetime | None = None,
    ) -> SandboxRunResult:
        active_now = _utc_now(now)
        started = time.monotonic()
        prepared = self._prepare(task_id, candidate, context_artifact, now=active_now)
        if isinstance(prepared, str):
            status: Literal["rejected", "resource_limited"] = (
                "resource_limited" if prepared.startswith("resource_limit:") else "rejected"
            )
            return self._finalize(
                task_id,
                self._result(candidate.candidate_id, status, started, reason=prepared.removeprefix("resource_limit: ")),
                now=active_now,
            )
        source, context = prepared
        safety = self.safety_policy.inspect(
            SafetyRequest(
                source="signal_sandbox",
                actor_type="agent",
                requested_capability="run_signal_sandbox",
                raw_text=source,
            )
        )
        if not safety.allowed:
            return self._finalize(
                task_id,
                self._result(candidate.candidate_id, "rejected", started, reason=f"safety:{safety.reason_code}"),
                now=active_now,
                audit_id=safety.audit_id,
            )
        request = {
            "source": source,
            "context": context.model_dump(mode="json"),
            "timeout_seconds": self.policy.timeout_seconds,
            "memory_limit_mb": self.policy.memory_limit_mb,
            "max_output_bytes": self.policy.max_output_bytes,
            "max_points": self.policy.max_points,
        }
        try:
            child = self._run_child(request)
        except subprocess.TimeoutExpired:
            return self._finalize(
                task_id,
                self._result(candidate.candidate_id, "timed_out", started, reason="candidate exceeded Sandbox timeout"),
                now=active_now,
            )
        except OSError:
            return self._finalize(
                task_id,
                self._result(candidate.candidate_id, "failed", started, reason="Sandbox child could not start"),
                now=active_now,
            )
        parsed = _parse_child_output(child.stdout)
        if child.returncode != 0 or parsed is None or parsed.get("status") != "ok":
            code = parsed.get("code") if parsed is not None else None
            status: Literal["failed", "resource_limited"] = "resource_limited" if code == "resource_limit" or child.returncode < 0 else "failed"
            reason = str(parsed.get("message", "Sandbox child failed")) if parsed is not None else "Sandbox child returned invalid JSON"
            return self._finalize(
                task_id,
                self._result(candidate.candidate_id, status, started, exit_code=child.returncode, reason=reason),
                now=active_now,
            )
        points = parsed.get("points")
        if not isinstance(points, list):
            return self._finalize(
                task_id,
                self._result(
                    candidate.candidate_id,
                    "failed",
                    started,
                    exit_code=child.returncode,
                    reason="Sandbox output omitted points",
                ),
                now=active_now,
            )
        artifact = self.artifact_service.save_json(
            task_id,
            kind="validation_metrics",
            payload={"candidate_id": candidate.candidate_id, "points": points},
            source="signal_sandbox:points",
            created_at=active_now,
        )
        return self._finalize(
            task_id,
            self._result(
                candidate.candidate_id,
                "succeeded",
                started,
                exit_code=child.returncode,
                point_artifact=artifact,
                point_count=len(points),
            ),
            now=active_now,
        )

    def _prepare(
        self,
        task_id: str,
        candidate: CandidateFunction,
        context_artifact: ArtifactRef,
        *,
        now: datetime,
    ) -> tuple[str, SignalContext] | str:
        stored = self.repository.get_candidate(candidate.candidate_id)
        provenance = self.repository.get_build_provenance(candidate.candidate_id)
        if stored != candidate or provenance is None or provenance.task_id != task_id:
            return "Candidate is not a persisted evidence-backed build"
        try:
            evidence_service = EvidenceService(self.artifact_service.store.connection, self.artifact_service.store)
            canonical = [
                evidence_service.get(task_id, reference.evidence_id, now=now)
                for reference in provenance.proposal.evidence_refs
            ]
            if canonical != provenance.proposal.evidence_refs:
                return "Candidate proposal evidence does not match stored task evidence"
            evidence_bundle = evidence_service.build_bundle(task_id, canonical, now=now)
            for artifact in evidence_bundle.artifact_refs:
                self.artifact_service.open_bytes(task_id, artifact)
            source = self.artifact_service.open_bytes(task_id, candidate.source_artifact).decode("utf-8")
            payload = self.artifact_service.load_json(task_id, context_artifact)
            context = SignalContext.model_validate(payload)
            context.validate_catalog(provenance.feature_catalog)
            referenced = validate_candidate_source(source, allowed_features=provenance.feature_catalog.names)
            proposal_features = proposal_feature_names(
                provenance.feature_catalog,
                [feature.name for feature in provenance.proposal.features],
            )
            if not referenced.issubset(proposal_features):
                return "Candidate source requires a feature absent from its proposal"
            if _requests_excessive_literal_allocation(source, max_elements=self.policy.memory_limit_mb * 250_000):
                return "resource_limit: Candidate requests an allocation beyond the Sandbox memory policy"
        except (UnicodeDecodeError, ValueError, AstPolicyError):
            return "Candidate source or SignalContext violates the Sandbox policy"
        except Exception:
            return "Candidate source or SignalContext artifact is unavailable or fails integrity checks"
        return source, context

    def _run_child(self, request: dict[str, object]) -> subprocess.CompletedProcess[bytes]:
        child_path = Path(__file__).with_name("child_runner.py")
        with tempfile.TemporaryDirectory(prefix="stock-agent-sandbox-") as temporary_dir:
            environment = {"HOME": temporary_dir, "PATH": os.defpath, "TMPDIR": temporary_dir}
            return subprocess.run(
                [sys.executable, "-I", str(child_path)],
                input=json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=temporary_dir,
                env=environment,
                timeout=self.policy.timeout_seconds + 0.5,
                check=False,
            )

    def _result(
        self,
        candidate_id: str,
        status: Literal["succeeded", "rejected", "failed", "timed_out", "resource_limited"],
        started: float,
        *,
        exit_code: int | None = None,
        point_artifact: ArtifactRef | None = None,
        point_count: int = 0,
        reason: str | None = None,
    ) -> SandboxRunResult:
        return SandboxRunResult(
            candidate_id=candidate_id,
            status=status,
            policy_version=self.policy.version,
            duration_ms=round((time.monotonic() - started) * 1000),
            exit_code=exit_code,
            point_artifact=point_artifact,
            point_count=point_count,
            reason=reason,
        )

    def _finalize(
        self,
        task_id: str,
        result: SandboxRunResult,
        *,
        now: datetime,
        audit_id: str | None = None,
    ) -> SandboxRunResult:
        """Record resource use without exposing candidate source or input market data."""

        try:
            self.budget_ledger.consume(
                task_id,
                sandbox_cpu_ms=result.duration_ms,
                sandbox_memory_mb_ms=result.duration_ms * self.policy.memory_limit_mb,
                now=now,
            )
            self.trace_recorder.record(
                AgentTrace(
                    trace_id=f"trace-sandbox-{result.candidate_id}-{uuid4().hex}",
                    task_id=task_id,
                    component="sandbox",
                    status="success" if result.status == "succeeded" else "failed",
                    duration_ms=result.duration_ms,
                    input_ref={"candidate_id": result.candidate_id, "policy_version": result.policy_version},
                    output_ref={
                        "status": result.status,
                        "exit_code": result.exit_code,
                        "point_artifact_id": result.point_artifact.artifact_id if result.point_artifact else None,
                        "point_count": result.point_count,
                        "audit_id": audit_id,
                    },
                    error_message=result.reason,
                    created_at=now,
                )
            )
        except Exception:
            # Diagnostics must never make an already-isolated sandbox result unavailable.
            pass
        return result


def _parse_child_output(value: bytes) -> dict[str, object] | None:
    if len(value) > 1_048_576:
        return None
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _requests_excessive_literal_allocation(source: str, *, max_elements: int) -> bool:
    """Reject obvious sequence bombs before macOS can overcommit their child heap."""

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError:
        return True
    for node in ast.walk(module):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Mult):
            continue
        left, right = node.left, node.right
        if isinstance(left, (ast.List, ast.Tuple, ast.Set, ast.Constant)) and isinstance(right, ast.Constant):
            if isinstance(right.value, int) and right.value > max_elements:
                return True
        if isinstance(right, (ast.List, ast.Tuple, ast.Set, ast.Constant)) and isinstance(left, ast.Constant):
            if isinstance(left.value, int) and left.value > max_elements:
                return True
    return False


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("Sandbox time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["CandidateSandbox", "SandboxPolicy", "SandboxRunResult"]
