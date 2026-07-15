"""FastAPI transport adapter for the durable V2 research lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.input_gate import InputGate, InputGateError
from stock_agent.services.entrypoints import ResearchEntryAdapter, ResearchEntryError
from stock_agent.storage.sqlite import initialize_runtime_database

if TYPE_CHECKING:
    from stock_agent.contracts.tasks import ResearchRequest
    from stock_agent.services.agent_service import AgentService


class WebAgentError(ValueError):
    """The web request cannot safely enter the V2 research lifecycle."""


class WebAgentService:
    """Keep HTTP concerns outside the shared AgentService boundary."""

    def __init__(
        self,
        root: Path,
        *,
        config_context: RuntimeConfigContext | None = None,
        v2_agent_service: "AgentService | None" = None,
    ) -> None:
        self.root = root
        self.config_context = config_context or load_config(root)
        self.research_entry = ResearchEntryAdapter(v2_agent_service) if v2_agent_service is not None else None

    def submit_research(self, request: "ResearchRequest", *, actor_ref: str) -> dict[str, object]:
        self._require_input(actor_ref)
        try:
            return self._entry().submit(request, source="web", actor_ref=actor_ref)
        except ResearchEntryError as exc:
            raise WebAgentError(str(exc)) from exc

    def research_status(self, task_id: str, *, actor_ref: str) -> dict[str, object]:
        try:
            return self._entry().status(task_id, source="web", actor_ref=actor_ref)
        except ResearchEntryError as exc:
            raise WebAgentError(str(exc)) from exc

    def provide_research_input(
        self,
        task_id: str,
        step_id: str,
        payload: dict[str, object],
        *,
        actor_ref: str,
    ) -> dict[str, object]:
        self._require_input(actor_ref)
        try:
            return self._entry().provide_input(task_id, step_id, payload, source="web", actor_ref=actor_ref)
        except ResearchEntryError as exc:
            raise WebAgentError(str(exc)) from exc

    def control_research(self, task_id: str, action: str, *, actor_ref: str) -> dict[str, object]:
        self._require_input(actor_ref)
        try:
            return self._entry().control(task_id, action, source="web", actor_ref=actor_ref)
        except ResearchEntryError as exc:
            raise WebAgentError(str(exc)) from exc

    def research_report(self, task_id: str, report_id: str | None, *, actor_ref: str) -> dict[str, object]:
        try:
            return self._entry().report(task_id, report_id, source="web", actor_ref=actor_ref)
        except ResearchEntryError as exc:
            raise WebAgentError(str(exc)) from exc

    def input_state(self) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            return self._input_gate(connection).state().as_dict()
        finally:
            connection.close()

    def heartbeat(self, *, actor_ref: str) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            gate = self._input_gate(connection)
            gate.heartbeat("fastapi", actor_ref=actor_ref)
            return gate.state().as_dict()
        finally:
            connection.close()

    def request_input_switch(self, *, actor_ref: str) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            return self._input_gate(connection).request_switch("fastapi", actor_ref=actor_ref).as_dict()
        except InputGateError as exc:
            raise WebAgentError(str(exc)) from exc
        finally:
            connection.close()

    def decide_input_switch(self, request_id: str, *, actor_ref: str, approve: bool) -> dict[str, object]:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            return self._input_gate(connection).decide(
                request_id,
                source="fastapi",
                actor_ref=actor_ref,
                approve=approve,
            ).as_dict()
        except InputGateError as exc:
            raise WebAgentError(str(exc)) from exc
        finally:
            connection.close()

    def _require_input(self, actor_ref: str) -> None:
        connection = initialize_runtime_database(self.root, self.config_context.config)
        try:
            decision = self._input_gate(connection).check("fastapi", actor_ref=actor_ref)
        finally:
            connection.close()
        if not decision.allowed:
            raise WebAgentError(decision.message)

    def _entry(self) -> ResearchEntryAdapter:
        if self.research_entry is None:
            raise WebAgentError("V2 AgentService is not configured for this Web entry point")
        return self.research_entry

    def _input_gate(self, connection) -> InputGate:
        return InputGate.from_config(connection, self.config_context.config.input_control)


__all__ = ["WebAgentError", "WebAgentService"]
