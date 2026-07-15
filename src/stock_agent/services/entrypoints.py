"""Transport-neutral, safety-checked V2 research entry boundary."""

from __future__ import annotations

import json
from typing import Any

from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.security.research_policy import SafetyRequest
from stock_agent.services.agent_service import AgentService, AgentServiceError
from stock_agent.signals.approval import ApprovalRequest
from stock_agent.signals.registry import SignalRegistryError
from stock_agent.storage.report_repository import ReportRepository


class ResearchEntryError(ValueError):
    """A safe, transport-independent error for V2 research entry points."""


class ResearchEntryAdapter:
    """Keep CLI, Web, and Telegram from duplicating lifecycle and report behavior."""

    def __init__(self, service: AgentService) -> None:
        self.service = service

    def submit(
        self,
        request: ResearchRequest,
        *,
        source: str,
        actor_ref: str,
        actor_type: str = "human_user",
    ) -> dict[str, object]:
        with self.service.lock:
            self._authorize(
                source=source,
                actor_ref=actor_ref,
                actor_type=actor_type,
                raw_text=request.question,
            )
            try:
                task = self.service.submit(request)
            except AgentServiceError as exc:
                raise ResearchEntryError(str(exc)) from exc
            return self.status(task_id=task.task_id, source=source, actor_ref=actor_ref, actor_type=actor_type)

    def status(
        self,
        task_id: str,
        *,
        source: str,
        actor_ref: str,
        actor_type: str = "human_user",
    ) -> dict[str, object]:
        with self.service.lock:
            self._authorize(source=source, actor_ref=actor_ref, actor_type=actor_type)
            try:
                status = self.service.get(task_id)
            except AgentServiceError as exc:
                raise ResearchEntryError(str(exc)) from exc
            report = ReportRepository(self.service.connection).get_latest_final_for_task(task_id)
            return {
                **status,
                "report_id": report.report_id if report is not None else None,
            }

    def provide_input(
        self,
        task_id: str,
        step_id: str,
        payload: dict[str, Any],
        *,
        source: str,
        actor_ref: str,
        actor_type: str = "human_user",
    ) -> dict[str, object]:
        with self.service.lock:
            self._authorize(
                source=source,
                actor_ref=actor_ref,
                actor_type=actor_type,
                raw_text=_safe_json(payload),
            )
            try:
                self.service.provide_input(task_id, step_id, payload)
            except AgentServiceError as exc:
                raise ResearchEntryError(str(exc)) from exc
            return self.status(task_id, source=source, actor_ref=actor_ref, actor_type=actor_type)

    def control(
        self,
        task_id: str,
        action: str,
        *,
        source: str,
        actor_ref: str,
        actor_type: str = "human_user",
    ) -> dict[str, object]:
        with self.service.lock:
            self._authorize(source=source, actor_ref=actor_ref, actor_type=actor_type)
            operations = {
                "pause": self.service.pause,
                "resume": self.service.resume,
                "cancel": self.service.cancel,
                "retry-report": self.service.retry_report_after_validation,
            }
            if action not in operations:
                raise ResearchEntryError("unsupported research control action")
            try:
                operations[action](task_id)
            except AgentServiceError as exc:
                raise ResearchEntryError(str(exc)) from exc
            return self.status(task_id, source=source, actor_ref=actor_ref, actor_type=actor_type)

    def report(
        self,
        task_id: str,
        report_id: str | None = None,
        *,
        source: str,
        actor_ref: str,
        actor_type: str = "human_user",
    ) -> dict[str, object]:
        with self.service.lock:
            self._authorize(source=source, actor_ref=actor_ref, actor_type=actor_type)
            self.status(task_id, source=source, actor_ref=actor_ref, actor_type=actor_type)
            repository = ReportRepository(self.service.connection)
            report = repository.get_final(report_id) if report_id else repository.get_latest_final_for_task(task_id)
            if report is None:
                raise ResearchEntryError("final report does not exist for this task")
            if report.draft.task_id != task_id:
                raise ResearchEntryError("final report does not belong to this task")
            return report.model_dump(mode="json")

    def approve_signal(
        self,
        task_id: str,
        *,
        signal_id: str,
        version: int,
        reason: str,
        source: str,
        actor_ref: str,
        actor_type: str,
    ) -> dict[str, object]:
        """Expose the existing human-only signal approval boundary to transports."""

        if actor_type != "human_admin":
            raise ResearchEntryError("signal approval requires an authenticated human admin")
        with self.service.lock:
            decision = self.service.safety_policy.inspect(
                SafetyRequest(
                    source=source,
                    actor_ref=actor_ref,
                    actor_type="human_admin",
                    requested_capability="approve_signal",
                    raw_text=reason,
                )
            )
            if not decision.allowed:
                raise ResearchEntryError(f"signal approval is blocked: {decision.reason_code}")
            try:
                version_result, approval = self.service.approve(
                    task_id,
                    ApprovalRequest(
                        signal_id=signal_id,
                        version=version,
                        decided_by=actor_ref,
                        actor_role="admin",
                        reason=reason,
                    ),
                )
            except (AgentServiceError, SignalRegistryError) as exc:
                raise ResearchEntryError(str(exc)) from exc
            return {
                "signal_version": version_result.model_dump(mode="json"),
                "approval": approval.model_dump(mode="json"),
            }

    def _authorize(
        self,
        *,
        source: str,
        actor_ref: str,
        actor_type: str,
        raw_text: str | None = None,
    ) -> None:
        decision = self.service.safety_policy.inspect(
            SafetyRequest(
                source=source,
                actor_ref=actor_ref,
                actor_type=actor_type,  # type: ignore[arg-type]
                requested_capability="research",
                raw_text=raw_text,
            )
        )
        if not decision.allowed:
            audit_id = f" audit_id={decision.audit_id}" if decision.audit_id else ""
            raise ResearchEntryError(f"research is blocked: {decision.reason_code}.{audit_id}")

def _safe_json(payload: dict[str, Any]) -> str:
    """Bound policy inspection input while preserving the original typed payload."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)[:20_000]


__all__ = ["ResearchEntryAdapter", "ResearchEntryError"]
