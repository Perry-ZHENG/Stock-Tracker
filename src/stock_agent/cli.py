"""Command-line entry point for Stock Agent."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from stock_agent import __version__
from stock_agent.config import init_config

COMMANDS: dict[str, str] = {
    "init-config": "Generate default configs/config.yaml and .env.example.",
    "run-demo": "Run the offline CSV demo flow end to end.",
    "cli": "Start interactive CLI query and review mode.",
    "telegram": "Start the Telegram bot listener.",
    "worker": "Start background data, strategy, signal, and health workers.",
    "health": "Print current system health and recent errors.",
}


def _not_implemented(command: str) -> int:
    print(
        f"stock-agent {command}: command skeleton is available, "
        "but implementation is scheduled for a later task."
    )
    return 2


def _command_handler(command: str):
    return lambda _args: _not_implemented(command)


def _handle_init_config(args: argparse.Namespace) -> int:
    result = init_config(Path.cwd(), force=args.force)
    config_status = "created" if result.config_written else "exists"
    env_status = "created" if result.env_example_written else "exists"
    print(f"{config_status}: {result.config_path}")
    print(f"{env_status}: {result.env_example_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-agent",
        description="Local-first US market watch and signal assistant.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
        required=False,
    )
    for command, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(
            command,
            help=help_text,
            description=help_text,
        )
        if command == "init-config":
            subparser.add_argument(
                "--force",
                action="store_true",
                help="Overwrite existing config files.",
            )
            subparser.set_defaults(handler=_handle_init_config)
        else:
            subparser.set_defaults(handler=_command_handler(command))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
