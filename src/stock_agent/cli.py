"""Command-line entry point for Stock Agent."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from stock_agent import __version__
from stock_agent.commands.bars import run_bars_query
from stock_agent.commands.config_review import run_config_review
from stock_agent.commands.health import run_health
from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.commands.query_cli import run_cli_query
from stock_agent.commands.replay import run_replay
from stock_agent.commands.run_demo import run_demo
from stock_agent.commands.telegram import run_telegram
from stock_agent.commands.trace import run_trace_query
from stock_agent.commands.worker import run_worker
from stock_agent.config import init_config
from stock_agent.config_loader import load_config

COMMANDS: dict[str, str] = {
    "init-config": "Generate default configs/config.yaml and .env.example.",
    "run-demo": "Run the offline CSV demo flow end to end.",
    "cli": "Start interactive CLI query and review mode.",
    "telegram": "Start the Telegram bot listener.",
    "worker": "Start background data, strategy, signal, and health workers.",
    "health": "Print current system health and recent errors.",
    "replay": "Replay historical bars from the lake and recalculate signals.",
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


def _argument_config_path(root: Path, args: argparse.Namespace) -> Path | None:
    config_path = getattr(args, "config_path", None)
    if config_path is None:
        return _runtime_config_path(root)
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
    root = _runtime_root()
    run_demo(root, config_context=load_config(root))
    return 0


def _handle_health(_args: argparse.Namespace) -> int:
    root = _runtime_root()
    result = run_health(root, config_context=load_config(root))
    return 0 if result.status != "unhealthy" else 1


def _handle_telegram(_args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_telegram(root, config_context=load_config(root))


def _handle_worker(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_worker(
        root,
        once=args.once,
        interval_sec=args.interval_sec,
        config_context=load_config(root, _argument_config_path(root, args)),
    )


def _handle_cli_query(args: argparse.Namespace) -> int:
    root = _runtime_root()
    config_context = load_config(root)
    if args.action is None:
        return run_interactive_cli(root, config_context=config_context)
    if args.action == "bars":
        result = run_bars_query(
            root,
            symbol=args.symbol,
            from_value=args.from_ts,
            to_value=args.to_ts,
            config_context=config_context,
        )
        return 0 if result.ok else 1
    if args.action == "trace":
        result = run_trace_query(
            root,
            args.change_id,
            config_context=config_context,
        )
        return 0 if result.ok else 1
    if args.action in {"review", "approve", "reject"}:
        return run_config_review(
            root,
            action=args.action,
            change_id=args.change_id,
            limit=args.limit,
            config_path=_runtime_config_path(root),
            config_context=config_context,
        )
    return run_cli_query(
        root,
        query=args.action,
        limit=args.limit,
        symbol=args.symbol,
        period=args.period,
        config_context=config_context,
    )


def _handle_replay(args: argparse.Namespace) -> int:
    root = _runtime_root()
    result = run_replay(
        root,
        from_value=args.from_ts,
        to_value=args.to_ts,
        symbols=args.symbols or [],
        persist=args.persist,
        report_path=args.report,
        config_context=load_config(root),
    )
    return 0 if result.ok else 1


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
                choices=(
                    "signals",
                    "health",
                    "config-changes",
                    "news",
                    "stats",
                    "schedule",
                    "bars",
                    "trace",
                    "review",
                    "approve",
                    "reject",
                ),
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
                "--from",
                dest="from_ts",
                help="Inclusive UTC start for bars query, e.g. 2026-05-22T13:30:00Z.",
            )
            subparser.add_argument(
                "--to",
                dest="to_ts",
                help="Inclusive UTC end for bars query, e.g. 2026-05-22T20:00:00Z.",
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
            subparser.add_argument(
                "--config",
                dest="config_path",
                type=Path,
                help="Runtime config path for this worker invocation.",
            )
            subparser.set_defaults(handler=_handle_worker)
        elif command == "replay":
            subparser.add_argument(
                "--from",
                dest="from_ts",
                help="Inclusive UTC start, e.g. 2026-05-22T13:30:00Z.",
            )
            subparser.add_argument(
                "--to",
                dest="to_ts",
                help="Inclusive UTC end, e.g. 2026-05-22T20:00:00Z.",
            )
            subparser.add_argument(
                "--symbols",
                nargs="+",
                required=True,
                help="Symbols to replay, e.g. --symbols QQQ SPY.",
            )
            subparser.add_argument(
                "--persist",
                action="store_true",
                help="Persist replay signals and audit traces into SQLite.",
            )
            subparser.add_argument(
                "--report",
                type=Path,
                help="Optional regression report path to write as JSON.",
            )
            subparser.set_defaults(handler=_handle_replay)
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
