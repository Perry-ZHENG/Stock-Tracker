"""Command-line entry point for Stock Agent."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from stock_agent import __version__
from stock_agent.commands.config_review import run_config_review
from stock_agent.commands.health import run_health
from stock_agent.commands.query_cli import run_cli_query
from stock_agent.commands.run_demo import run_demo
from stock_agent.commands.telegram import run_telegram
from stock_agent.commands.worker import run_worker
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
# This is a placeholder for commands that are not yet implemented. It prints a message and returns a non-zero exit code.
    print(
        f"stock-agent {command}: command skeleton is available, "
        "but implementation is scheduled for a later task."
    )
    return 2


def _command_handler(command: str):
    #  This function returns a handler that prints a not implemented message.
    return lambda _args: _not_implemented(command)


def _runtime_root() -> Path:
    """Return the project root used by CLI commands.

    Deployment managers such as launchd, systemd, and pm2 can set
    STOCK_AGENT_WORKDIR instead of relying on the current shell directory.
    """
    workdir = os.getenv("STOCK_AGENT_WORKDIR")
    if workdir:
        return Path(workdir).expanduser()
    return Path.cwd()


def _runtime_config_path(root: Path) -> Path | None:
    config_path = os.getenv("STOCK_AGENT_CONFIG")
    if not config_path:
        return None
    path = Path(config_path).expanduser()
    if path.is_absolute():
        return path
    return root / path


def _handle_init_config(args: argparse.Namespace) -> int:
    # init-config is implemented. It calls the init_config function and prints the results.
    root = _runtime_root()
    result = init_config(root, force=args.force, config_path=_runtime_config_path(root))
    config_status = "created" if result.config_written else "exists"
    env_status = "created" if result.env_example_written else "exists"
    print(f"{config_status}: {result.config_path}")
    print(f"{env_status}: {result.env_example_path}")
    return 0


def _handle_run_demo(_args: argparse.Namespace) -> int:
    run_demo(_runtime_root())
    return 0


def _handle_health(_args: argparse.Namespace) -> int:
    result = run_health(_runtime_root())
    return 0 if result.status != "unhealthy" else 1


def _handle_telegram(_args: argparse.Namespace) -> int:
    return run_telegram(_runtime_root())


def _handle_worker(args: argparse.Namespace) -> int:
    return run_worker(_runtime_root(), once=args.once, interval_sec=args.interval_sec)


def _handle_cli_query(args: argparse.Namespace) -> int:
    root = _runtime_root()
    if args.action in {"review", "approve", "reject"}:
        return run_config_review(
            root,
            action=args.action,
            change_id=args.change_id,
            limit=args.limit,
            config_path=_runtime_config_path(root),
        )
    return run_cli_query(
        root,
        query=args.action,
        limit=args.limit,
        symbol=args.symbol,
        period=args.period,
    )


def build_parser() -> argparse.ArgumentParser:
    # build the argument parser with subcommands and their handlers
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
        elif command == "run-demo":
            subparser.set_defaults(handler=_handle_run_demo)
        elif command == "health":
            subparser.set_defaults(handler=_handle_health)
        elif command == "cli":
            subparser.add_argument(
                "action",
                choices=("signals", "health", "config-changes", "news", "stats", "review", "approve", "reject"),
                nargs="?",
                help="Read-only query or config review action to run.",
            )
            subparser.add_argument(
                "change_id",
                nargs="?",
                help="Config change id for review, approve, or reject.",
            )
            subparser.add_argument(
                "--limit",
                type=int,
                default=10,
                help="Maximum rows to display.",
            )
            subparser.add_argument(
                "--symbol",
                help="Optional symbol for news query.",
            )
            subparser.add_argument(
                "--period",
                choices=("day", "month", "year"),
                default="day",
                help="Statistics period for stats query.",
            )
            subparser.set_defaults(handler=_handle_cli_query)
        elif command == "telegram":
            subparser.set_defaults(handler=_handle_telegram)
        elif command == "worker":
            subparser.add_argument(
                "--once",
                action="store_true",
                help="Run one worker tick and exit.",
            )
            subparser.add_argument(
                "--interval-sec",
                type=float,
                default=30,
                help="Seconds between worker ticks.",
            )
            subparser.set_defaults(handler=_handle_worker)
        else:
            subparser.set_defaults(handler=_command_handler(command))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # main function to parse arguments and call the appropriate handler
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
