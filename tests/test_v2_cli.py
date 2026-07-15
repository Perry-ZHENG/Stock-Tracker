from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.cli import main
from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.services.agent_service import AgentService
from stock_agent.services.entrypoints import ResearchEntryAdapter
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class Output(StrictSchema):
    value: str


class NoInputHandler:
    def run(self, context, _typed_input) -> Output:
        return Output(value=context.step.actor)


def test_one_shot_cli_and_interactive_cli_share_the_same_task(tmp_path: Path, capsys) -> None:
    connection, service = _service(tmp_path)
    request_json = json.dumps(_request().model_dump(mode="json"))
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        assert main(["research", "submit", "--request-json", request_json], v2_agent_service=service) == 0
        submitted = capsys.readouterr().out
        task_id = _value(submitted, "task_id")
        assert main(["research", "status", task_id], v2_agent_service=service) == 0
        assert f"task_id={task_id}" in capsys.readouterr().out

    output = io.StringIO()
    exit_code = run_interactive_cli(
        tmp_path,
        input_stream=io.StringIO(f"research cancel {task_id}\nexit\n"),
        output_stream=output,
        research_entry=ResearchEntryAdapter(service),
    )
    connection.close()

    assert exit_code == 0
    assert f"task_id={task_id}" in output.getvalue()
    assert "status=cancelled" in output.getvalue()


def _service(root: Path) -> tuple[object, AgentService]:
    connection = initialize_runtime_database(root)
    repository = TaskRepository(connection)
    registry = AgentRegistry()
    for role in ("orchestrator", "signal_discovery", "anomaly_analysis", "macro_analysis", "report"):
        registry.register(AgentRegistration(role=role, handler=NoInputHandler(), output_schema=Output))
    runtime = AgentRuntime(
        repository=repository,
        artifact_service=ArtifactService(ArtifactStore(connection, root / "lake")),
        registry=registry,
    )
    return connection, AgentService(connection, runtime=runtime)


def _request() -> ResearchRequest:
    return ResearchRequest(
        request_id="request-v2-cli",
        question="Create a bounded QQQ research report.",
        symbols=["QQQ"],
        time_window=TimeWindow(
            from_ts=NOW - timedelta(days=1),
            to_ts=NOW,
            timezone="America/New_York",
        ),
    )


def _value(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    raise AssertionError(f"missing {key} in {text}")
