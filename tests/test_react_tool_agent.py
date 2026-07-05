import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.agent import (
    AgentToolContext,
    ReactToolAgent,
    build_default_tool_registry,
    parse_react_response,
)
from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal
from stock_agent.storage.sqlite import initialize_runtime_database


class SequenceModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class ReactToolAgentTests(unittest.TestCase):
    def test_parses_tool_call_and_finish(self) -> None:
        tool = parse_react_response(
            'Thought: 查询信号。\nAction: query_signals[{"symbol":"QQQ","limit":10}]'
        )
        finish = parse_react_response(
            "Thought: 已取得结果。\nAction: Finish[查询完成。]"
        )

        self.assertEqual(tool.name, "query_signals")
        self.assertEqual(tool.arguments["symbol"], "QQQ")
        self.assertEqual(finish.final_answer, "查询完成。")

    def test_runs_query_tool_then_returns_script_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            connection.close()
            model = SequenceModel(
                [
                    (
                        "Thought: 查询 QQQ 的 MACD 信号。\n"
                        'Action: query_signals[{"symbol":"QQQ","strategy_id":"macd","limit":10}]'
                    )
                ]
            )
            agent = ReactToolAgent(
                model_client=model,
                registry=build_default_tool_registry(),
                context=AgentToolContext.load(root),
            )

            result = agent.run("查询 QQQ 的 MACD 信号")

        self.assertTrue(result.ok)
        self.assertEqual(result.selected_tool, "query_signals")
        self.assertEqual(result.tool_calls[0].observation["count"], 1)
        self.assertIn("sig-macd-001", result.output)
        self.assertEqual(len(model.prompts), 1)

    def test_missing_argument_uses_ask_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            model = SequenceModel(
                [
                    (
                        "Thought: 查询 K 线缺少股票代码。\n"
                        'Action: ask_user[{"question":"请提供股票代码。","missing":["symbol"]}]'
                    )
                ]
            )
            agent = ReactToolAgent(
                model_client=model,
                registry=build_default_tool_registry(),
                context=AgentToolContext.load(Path(tmp_dir)),
            )

            result = agent.run("查询今天的 K 线")

        self.assertEqual(result.status, "needs_user_input")
        self.assertEqual(result.selected_tool, "ask_user")
        self.assertEqual(result.output, "请提供股票代码。")

    def test_unsupported_strategy_creation_returns_no_suitable_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            model = SequenceModel(
                [
                    (
                        "Thought: 当前没有创建新策略的注册工具。\n"
                        "Action: no_suitable_tool["
                        '{"reason":"当前没有新增 Order Book Imbalance 策略的工具"}]'
                    )
                ]
            )
            agent = ReactToolAgent(
                model_client=model,
                registry=build_default_tool_registry(),
                context=AgentToolContext.load(Path(tmp_dir)),
            )

            result = agent.run("新增 Order Book Imbalance 信号")

        self.assertEqual(result.status, "no_suitable_tool")
        self.assertEqual(result.selected_tool, "no_suitable_tool")
        self.assertIn("Order Book Imbalance", result.output)

    def test_model_rate_limit_returns_controlled_failure(self) -> None:
        class RateLimited(Exception):
            status_code = 429

        def model(_prompt: str) -> str:
            raise RateLimited("429 Too Many Requests")

        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = ReactToolAgent(
                model_client=model,
                registry=build_default_tool_registry(),
                context=AgentToolContext.load(Path(tmp_dir)),
            )

            result = agent.run("查询健康状态")

        self.assertEqual(result.status, "failed")
        self.assertIn("限流", result.output)


def _signal() -> Signal:
    return Signal(
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
    )


if __name__ == "__main__":
    unittest.main()
