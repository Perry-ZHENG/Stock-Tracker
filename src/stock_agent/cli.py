"""Command-line entry point for Stock Agent."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from stock_agent import __version__
from stock_agent.commands.bars import run_bars_query
from stock_agent.commands.config_review import run_config_review
from stock_agent.commands.deploy_validate import run_deploy_validate
from stock_agent.commands.health import run_health
from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.commands.mcp_server import run_mcp_server
from stock_agent.commands.query_cli import run_cli_query
from stock_agent.commands.replay import run_replay
from stock_agent.commands.retention import run_retention
from stock_agent.commands.run_demo import run_demo
from stock_agent.commands.telegram import run_telegram
from stock_agent.commands.trace import run_trace_query
from stock_agent.commands.worker import run_worker
from stock_agent.config import init_config
from stock_agent.config_loader import load_config
from stock_agent.contracts.reports import FinalReport
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.dialog.input_gate import InputGate
from stock_agent.reports.renderers import render_report
from stock_agent.services.agent_service import AgentService
from stock_agent.services.entrypoints import ResearchEntryAdapter, ResearchEntryError
from stock_agent.storage.sqlite import initialize_runtime_database

COMMANDS: dict[str, str] = {
    "init-config": "Generate default configs/config.yaml and .env.example.",
    "run-demo": "Run the offline CSV demo flow end to end.",
    "cli": "Start interactive CLI query and review mode.",
    "telegram": "Start the Telegram bot listener.",
    "worker": "Start background data, strategy, signal, and health workers.",
    "health": "Print current system health and recent errors.",
    "replay": "Replay historical bars from the lake and recalculate signals.",
    "deploy-validate": "Run offline deployment dry-run validation.",
    "retention": "Review data retention actions; executes only with --execute.",
    "mcp-server": "Start the read-only Stock Agent MCP server over stdio.",
    "research": "Submit, inspect, control, or render a V2 research task.",
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


def _handle_health(args: argparse.Namespace) -> int:
    root = _runtime_root()
    result = run_health(root, config_context=load_config(root), verbose=args.verbose)
    return 0 if result.status != "unhealthy" else 1


def _handle_telegram(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_telegram(
        root,
        config_context=load_config(root),
        research_entry=getattr(args, "research_entry", None),
    )


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
        return run_interactive_cli(
            root,
            config_context=config_context,
            research_entry=getattr(args, "research_entry", None),
        )
    connection = initialize_runtime_database(root, config_context.config)
    gate = InputGate.from_config(connection, config_context.config.input_control)
    actor_ref = "cli:one-shot"
    decision = gate.check("cli", actor_ref=actor_ref)
    if not decision.allowed:
        print(f"input_status=blocked\nmessage={decision.message}")
        connection.close()
        return 3
    try:
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
        if args.action in {"agent-trace", "budget"}:
            result = run_cli_query(
                root,
                query=args.action,
                limit=args.limit,
                target_id=args.change_id,
                config_context=config_context,
            )
            return result
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
    finally:
        gate.mark_offline("cli", actor_ref=actor_ref)
        connection.close()


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


def _handle_deploy_validate(_args: argparse.Namespace) -> int:
    root = _runtime_root()
    result = run_deploy_validate(root, config_context=load_config(root))
    return 0 if result.ok else 1


def _handle_retention(args: argparse.Namespace) -> int:
    root = _runtime_root()
    result = run_retention(root, execute=args.execute, config_context=load_config(root))
    return 0 if result.ok else 1


def _handle_mcp_server(_args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_mcp_server(root, config_context=load_config(root))


def _handle_research(args: argparse.Namespace) -> int:
    entry = getattr(args, "research_entry", None)
    if entry is None:
        print("research_status=unavailable\nmessage=V2 AgentService is not configured")
        return 2

    root = _runtime_root()
    config_context = load_config(root)
    action = args.action
    if action != "submit" and not args.task_id:
        print(f"research_status=failed\nmessage=research {action} requires TASK_ID")
        return 2
    if action == "input" and (not args.step_id or not args.payload_json):
        print("research_status=failed\nmessage=research input requires --step-id and --payload-json")
        return 2
    connection = initialize_runtime_database(root, config_context.config)
    actor_ref = "cli:one-shot"
    requires_input = action in {"submit", "input", "pause", "resume", "cancel", "approve-signal"}
    try:
        if requires_input:
            decision = InputGate.from_config(connection, config_context.config.input_control).check(
                "cli",
                actor_ref=actor_ref,
            )
            if not decision.allowed:
                print(f"input_status=blocked\nmessage={decision.message}")
                return 3
        if action == "submit":
            payload = _research_request_payload(args)
            status = entry.submit(
                ResearchRequest.model_validate_json(payload),
                source="cli",
                actor_ref=actor_ref,
            )
            _print_research_status(status, action="submitted")
            return 0
        if action in {"status", "watch"}:
            status = entry.status(args.task_id, source="cli", actor_ref=actor_ref)
            _print_research_status(status, action=action)
            return 0
        if action in {"pause", "resume", "cancel"}:
            status = entry.control(args.task_id, action, source="cli", actor_ref=actor_ref)
            _print_research_status(status, action=action)
            return 0
        if action == "input":
            payload = json.loads(args.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("research input payload must be a JSON object")
            status = entry.provide_input(
                args.task_id,
                args.step_id,
                payload,
                source="cli",
                actor_ref=actor_ref,
            )
            _print_research_status(status, action="input_received")
            return 0
        if action == "report":
            report = entry.report(args.task_id, args.report_id, source="cli", actor_ref=actor_ref)
            if args.output_format == "markdown":
                print(render_report(FinalReport.model_validate(report), "markdown").decode("utf-8"))
            else:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if action == "approve-signal":
            if not args.signal_id or args.signal_version is None or not args.reason or not args.admin_ref:
                raise ValueError(
                    "research approve-signal requires --signal-id --signal-version --reason --admin-ref"
                )
            result = entry.approve_signal(
                args.task_id,
                signal_id=args.signal_id,
                version=args.signal_version,
                reason=args.reason,
                source="cli",
                actor_ref=args.admin_ref,
                actor_type="human_admin",
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
    except (ResearchEntryError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"research_status=failed\nmessage={exc}")
        return 1
    finally:
        if requires_input:
            InputGate.from_config(connection, config_context.config.input_control).mark_offline(
                "cli",
                actor_ref=actor_ref,
            )
        connection.close()
    return 2


def _research_request_payload(args: argparse.Namespace) -> str:
    if args.request_json:
        return args.request_json
    if args.request_file:
        return args.request_file.read_text(encoding="utf-8")
    raise ValueError("research submit requires --request-json or --request-file")


def _print_research_status(status: dict[str, object], *, action: str) -> None:
    task = status["task"]
    assert isinstance(task, dict)
    print(f"research_action={action}")
    print(f"task_id={task['task_id']}")
    print(f"status={task['status']}")
    print(f"report_id={status.get('report_id') or 'pending'}")


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
            subparser.add_argument(
                "--verbose",
                action="store_true",
                help="Print module-level observability details.",
            )
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
                    "provider-compare",
                    "abnormal-bars",
                    "bars",
                    "trace",
                    "agent-trace",
                    "budget",
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
        elif command == "deploy-validate":
            subparser.set_defaults(handler=_handle_deploy_validate)
        elif command == "retention":
            subparser.add_argument(
                "--execute",
                action="store_true",
                help="Apply reviewed retention actions. Without this flag the command is dry-run only.",
            )
            subparser.set_defaults(handler=_handle_retention)
        elif command == "mcp-server":
            subparser.set_defaults(handler=_handle_mcp_server)
        elif command == "research":
            subparser.add_argument(
                "action",
                choices=("submit", "status", "watch", "pause", "resume", "cancel", "input", "report", "approve-signal"),
                help="V2 research task action.",
            )
            subparser.add_argument(
                "task_id",
                nargs="?",
                help="Task id for every action except submit.",
            )
            subparser.add_argument(
                "--request-json",
                help="ResearchRequest JSON for submit.",
            )
            subparser.add_argument(
                "--request-file",
                type=Path,
                help="UTF-8 file containing ResearchRequest JSON for submit.",
            )
            subparser.add_argument(
                "--step-id",
                help="Step id for input.",
            )
            subparser.add_argument(
                "--payload-json",
                help="JSON object used by input.",
            )
            subparser.add_argument(
                "--report-id",
                help="Optional final report id; defaults to the task's latest final report.",
            )
            subparser.add_argument("--signal-id", help="Signal id for the human approval action.")
            subparser.add_argument("--signal-version", type=int, help="Signal version for the human approval action.")
            subparser.add_argument("--reason", help="Required human approval reason.")
            subparser.add_argument("--admin-ref", help="Authenticated configured admin reference for approval.")
            subparser.add_argument(
                "--format",
                dest="output_format",
                choices=("json", "markdown"),
                default="markdown",
                help="Output format for report.",
            )
            subparser.set_defaults(handler=_handle_research)
        else:
            subparser.set_defaults(handler=_command_handler(command))
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    v2_agent_service: AgentService | None = None,
) -> int:
    # main function to parse arguments and call the appropriate handler
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"cli", "research", "telegram"} and v2_agent_service is not None:
        args.research_entry = ResearchEntryAdapter(v2_agent_service)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
