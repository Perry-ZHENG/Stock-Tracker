import json
import tempfile
import unittest
from pathlib import Path

from stock_agent.agent import (
    AgentToolContext,
    build_default_tool_registry,
    render_react_prompt,
)
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal
from stock_agent.storage.sqlite import initialize_runtime_database
from datetime import UTC, datetime


class AgentToolDraftTests(unittest.TestCase):
    def test_prompt_contains_tools_question_history_and_observation(self) -> None:
        registry = build_default_tool_registry()

        prompt = render_react_prompt(
            tools=registry.prompt_text(),
            question="查询 QQQ 的 MACD 信号",
            history="用户此前指定 QQQ",
            observation="无",
        )

        self.assertIn("query_signals", prompt)
        self.assertIn("查询 QQQ 的 MACD 信号", prompt)
        self.assertIn("用户此前指定 QQQ", prompt)
        self.assertIn("Action:", prompt)

    def test_every_prompt_tool_has_json_schema(self) -> None:
        registry = build_default_tool_registry()

        for line in registry.prompt_text().splitlines():
            payload = json.loads(line)
            self.assertIn("name", payload)
            self.assertIn("parameters", payload)
            self.assertEqual(payload["parameters"]["type"], "object")

    def test_query_signals_tool_filters_symbol_strategy_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(
                connection,
                Signal(
                    signal_id="sig-macd-001",
                    strategy_id="macd",
                    symbol="QQQ",
                    timestamp=datetime(2026, 7, 3, 15, 30, tzinfo=UTC),
                    direction="buy_watch",
                    strength=0.7,
                    confidence=0.8,
                    reason="MACD crossed above signal",
                    trace_id="trace-macd-001",
                    source_bar_ids=["bar-001"],
                    data_quality="normal",
                    created_at=datetime(2026, 7, 3, 15, 30, tzinfo=UTC),
                ),
            )
            connection.close()
            registry = build_default_tool_registry()

            result = registry.invoke(
                "query_signals",
                context=AgentToolContext.load(root),
                arguments={
                    "symbol": "QQQ",
                    "strategy_id": "macd",
                    "trading_date": "2026-07-03",
                    "limit": 10,
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["signal_id"], "sig-macd-001")

    def test_missing_required_argument_is_rejected_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = build_default_tool_registry()
            context = AgentToolContext.load(Path(tmp_dir))

            with self.assertRaises(Exception):
                registry.invoke("query_bars", context=context, arguments={})


if __name__ == "__main__":
    unittest.main()
