from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.runtime import AgentRuntime
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.services.agent_service import AgentService
from stock_agent.services.entrypoints import ResearchEntryAdapter
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.telegram.bot import TelegramBot, TelegramBotSettings, TelegramUpdate


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


class Output(StrictSchema):
    value: str


class NoInputHandler:
    def run(self, context, _typed_input) -> Output:
        return Output(value=context.step.actor)


def test_telegram_returns_task_id_without_running_long_task(tmp_path: Path) -> None:
    connection, service = _service(tmp_path)
    bot = TelegramBot(
        root=tmp_path,
        connection=connection,
        settings=TelegramBotSettings(token="token", allowed_user_ids=[1], admin_user_ids=[], allowed_chat_ids=[]),
        research_entry=ResearchEntryAdapter(service),
    )
    submitted = bot.handle_update(
        TelegramUpdate(
            user_id=1,
            chat_id=100,
            text="/research submit " + json.dumps(_request().model_dump(mode="json")),
        )
    )
    task_id = _value(submitted.text, "task_id")
    status = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text=f"/research status {task_id}"))
    cancelled = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text=f"/research cancel {task_id}"))
    connection.close()

    assert submitted.ok
    assert "status=running" in submitted.text
    assert status.ok
    assert f"task_id={task_id}" in status.text
    assert cancelled.ok
    assert "status=cancelled" in cancelled.text


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
        request_id="request-v2-telegram",
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
