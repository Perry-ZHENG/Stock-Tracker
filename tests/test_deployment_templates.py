import unittest
from pathlib import Path


class DeploymentTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_templates_exist(self) -> None:
        expected_paths = [
            self.root / "deploy/launchd/com.example.stock-agent.worker.plist",
            self.root / "deploy/systemd/stock-agent-worker.service",
            self.root / "deploy/pm2/ecosystem.config.cjs",
            self.root / "docs/deployment.md",
        ]

        for path in expected_paths:
            with self.subTest(path=path):
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)

    def test_templates_do_not_hardcode_local_paths(self) -> None:
        deploy_files = [
            self.root / "deploy/launchd/com.example.stock-agent.worker.plist",
            self.root / "deploy/systemd/stock-agent-worker.service",
            self.root / "deploy/pm2/ecosystem.config.cjs",
        ]

        forbidden_fragments = ["/Users/", "/home/", "/Users/example"]
        for path in deploy_files:
            content = path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                with self.subTest(path=path, fragment=fragment):
                    self.assertNotIn(fragment, content)

    def test_templates_expose_runtime_environment(self) -> None:
        combined = "\n".join(
            [
                (self.root / "deploy/launchd/com.example.stock-agent.worker.plist").read_text(encoding="utf-8"),
                (self.root / "deploy/systemd/stock-agent-worker.service").read_text(encoding="utf-8"),
                (self.root / "deploy/pm2/ecosystem.config.cjs").read_text(encoding="utf-8"),
            ]
        )

        for expected in [
            "worker",
            "STOCK_AGENT_BIN",
            "STOCK_AGENT_WORKDIR",
            "STOCK_AGENT_CONFIG",
            "MARKET_DATA_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "NEWS_API_KEY",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)


if __name__ == "__main__":
    unittest.main()
