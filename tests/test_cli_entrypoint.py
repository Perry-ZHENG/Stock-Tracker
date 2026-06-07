import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_agent.cli import COMMANDS, build_parser, main


class CliEntrypointTests(unittest.TestCase):
    def test_build_parser_uses_stock_agent_program_name(self) -> None:
        parser = build_parser()

        self.assertEqual(parser.prog, "stock-agent")

    def test_main_without_args_exits_successfully(self) -> None:
        self.assertEqual(main([]), 0)

    def test_command_help_exits_successfully(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                with self.assertRaises(SystemExit) as exc_info:
                    main([command, "--help"])

                self.assertEqual(exc_info.exception.code, 0)

    def test_command_skeleton_returns_not_implemented(self) -> None:
        skeleton_commands = [
            command for command in COMMANDS if command not in {"init-config", "run-demo", "health", "cli", "telegram", "worker"}
        ]
        for command in skeleton_commands:
            with self.subTest(command=command):
                self.assertEqual(main([command]), 2)

    def test_init_config_command_creates_default_files(self) -> None:
        with TemporaryDirectory() as tmp_dir, patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
            self.assertEqual(main(["init-config"]), 0)

            self.assertTrue((Path(tmp_dir) / "configs" / "config.yaml").exists())
            self.assertTrue((Path(tmp_dir) / ".env.example").exists())


if __name__ == "__main__":
    unittest.main()
