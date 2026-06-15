import re
import unittest
from pathlib import Path


class ReadmeInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.readme = (self.root / "README.md").read_text(encoding="utf-8")

    def test_readme_documents_install_demo_and_test_commands(self) -> None:
        for expected in [
            "uv sync --extra dev",
            "pipx install .",
            "uv tool install .",
            "stock-agent init-config",
            "stock-agent run-demo",
            "stock-agent deploy-validate",
            "uv run --extra dev pytest",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.readme)

    def test_readme_avoids_local_paths_and_secret_values(self) -> None:
        forbidden = ["D:\\", "C:\\Users", "/Users/", "/home/", "TELEGRAM_BOT_TOKEN=", "MARKET_DATA_API_KEY="]
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.readme)

        self.assertIsNone(re.search(r"(sk-|xoxb-|ghp_)[A-Za-z0-9_\\-]{12,}", self.readme))


if __name__ == "__main__":
    unittest.main()
