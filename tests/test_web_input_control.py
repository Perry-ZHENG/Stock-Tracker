import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from stock_agent.agent import AgentToolContext, ReactToolAgent, build_default_tool_registry
from stock_agent.dialog.input_gate import InputGate
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.web import create_app
from stock_agent.web.agent_service import WebAgentService


class WebInputControlTests(unittest.TestCase):
    def test_web_agent_uses_react_tool_agent_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            model = lambda _prompt: (
                "Thought: 当前没有创建新策略的工具。\n"
                'Action: no_suitable_tool[{"reason":"当前没有新增该策略的工具"}]'
            )
            react_agent = ReactToolAgent(
                model_client=model,
                registry=build_default_tool_registry(),
                context=AgentToolContext.load(root),
            )
            service = WebAgentService(root, react_agent=react_agent)

            result = service.plan("新增 Order Book Imbalance 信号")

        self.assertEqual(result["parser_name"], "react_tool_agent")
        self.assertEqual(result["status"], "no_suitable_tool")
        self.assertEqual(result["selected_tool"], "no_suitable_tool")

    def test_home_page_exposes_input_control_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app = create_app(Path(tmp_dir))

            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("输入控制", response.text)
        self.assertIn("申请切换至 FastAPI", response.text)

    def test_web_is_blocked_until_original_interface_approves_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            app = create_app(root)

            with TestClient(app) as client:
                blocked = client.post("/api/v1/agent/plan", json={"message": "show signals"})
                requested = client.post("/api/v1/input/switch/requests")
                request_id = requested.json()["request_id"]

                gate.decide(
                    request_id,
                    source="cli",
                    actor_ref="cli-session",
                    approve=True,
                )
                allowed = client.post("/api/v1/agent/plan", json={"message": "show signals"})
                state = client.get("/api/v1/input")
            connection.close()

        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(blocked.json()["status"], "input_blocked")
        self.assertEqual(requested.status_code, 200)
        self.assertNotEqual(allowed.json()["status"], "input_blocked")
        self.assertEqual(state.json()["active_source"], "fastapi")

    def test_fastapi_cannot_approve_request_owned_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            InputGate(connection).check("cli", actor_ref="cli-session")
            app = create_app(root)

            with TestClient(app) as client:
                request_id = client.post("/api/v1/input/switch/requests").json()["request_id"]
                response = client.post(
                    f"/api/v1/input/switch/requests/{request_id}/approve"
                )
            connection.close()

        self.assertEqual(response.status_code, 409)
        self.assertIn("原输入接口", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
