"""Offline deployment dry-run validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from stock_agent.config_loader import RuntimeConfigContext, load_config


@dataclass(frozen=True)
class DeployValidationCheck:
    name: str
    status: str
    target: Path | str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class DeployValidationResult:
    root: Path
    config_path: Path
    checks: list[DeployValidationCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def validate_deployment(
    root: Path,
    *,
    config_context: RuntimeConfigContext | None = None,
) -> DeployValidationResult:
    checks: list[DeployValidationCheck] = []
    checks.append(_path_check("workdir", root, must_exist=True, message="project working directory"))

    config_context = config_context or load_config(root)
    config = config_context.config
    checks.append(
        DeployValidationCheck(
            name="config",
            status="error" if config_context.used_defaults else "ok",
            target=config_context.config_path,
            message="config file missing; run stock-agent init-config" if config_context.used_defaults else "config loaded",
        )
    )

    sqlite_path = root / config.storage.sqlite_path
    lake_root = root / config.storage.parquet_root
    duckdb_path = root / config.storage.duckdb_path
    checks.append(_parent_check("sqlite_parent", sqlite_path, "SQLite runtime directory"))
    checks.append(_parent_check("lake_parent", lake_root, "lake storage parent directory"))
    checks.append(_parent_check("duckdb_parent", duckdb_path, "DuckDB analytics directory"))

    csv_path = root / config.provider.csv_demo.path
    if config.provider.default == "csv_demo" or "csv_demo" in config.provider.priority:
        checks.append(_path_check("csv_demo", csv_path, must_exist=True, message="demo CSV data source"))

    return DeployValidationResult(root=root, config_path=config_context.config_path, checks=checks)


def format_deploy_validation(result: DeployValidationResult) -> str:
    lines = [
        f"deploy_validation_status={'ok' if result.ok else 'failed'}",
        "dry_run=true",
        f"root={result.root}",
        f"config_path={result.config_path}",
        "check | status | target | message",
    ]
    for check in result.checks:
        lines.append(" | ".join([check.name, check.status, str(check.target), check.message]))
    return "\n".join(lines) + "\n"


def _path_check(name: str, path: Path, *, must_exist: bool, message: str) -> DeployValidationCheck:
    if must_exist and not path.exists():
        return DeployValidationCheck(name=name, status="error", target=path, message=f"missing {message}")
    return DeployValidationCheck(name=name, status="ok", target=path, message=message)


def _parent_check(name: str, path: Path, message: str) -> DeployValidationCheck:
    parent = path if path.suffix == "" else path.parent
    if parent.exists():
        return DeployValidationCheck(name=name, status="ok", target=parent, message=message)
    return DeployValidationCheck(name=name, status="ok", target=parent, message=f"{message}; created by runtime when needed")


__all__ = [
    "DeployValidationCheck",
    "DeployValidationResult",
    "format_deploy_validation",
    "validate_deployment",
]
