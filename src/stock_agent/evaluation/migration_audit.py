"""Static import and runtime-trace audit used before removing legacy modules."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from pydantic import Field

from stock_agent.contracts.common import StrictSchema


class FileManifestEntry(StrictSchema):
    path: str
    classification: str
    importers: list[str] = Field(default_factory=list)
    runtime_hits: int = Field(ge=0)
    removal_gate: str
    decision: str


class MigrationAudit:
    """Never delete a file solely because a filename lacks a V2 suffix."""

    def __init__(self, root: Path, *, connection: sqlite3.Connection | None = None) -> None:
        self.root = root
        self.connection = connection

    def build_manifest(self) -> list[FileManifestEntry]:
        source_root = self.root / "src" / "stock_agent"
        paths = sorted(source_root.rglob("*.py"))
        imports = {path: _imports(path) for path in paths}
        entries: list[FileManifestEntry] = []
        for path in paths:
            relative = path.relative_to(source_root).as_posix()
            module = "stock_agent." + relative.removesuffix(".py").replace("/", ".")
            importers = sorted(
                other.relative_to(source_root).as_posix()
                for other, targets in imports.items()
                if module in targets or any(module.startswith(f"{target}.") for target in targets)
            )
            classification = _classification(relative)
            runtime_hits = self._runtime_hits(relative)
            removal_gate = "retain" if classification != "bridge_v2" else "no importers and no runtime trace hits"
            decision = "retain" if classification != "bridge_v2" or importers or runtime_hits else "eligible_for_review"
            entries.append(
                FileManifestEntry(
                    path=relative,
                    classification=classification,
                    importers=importers,
                    runtime_hits=runtime_hits,
                    removal_gate=removal_gate,
                    decision=decision,
                )
            )
        return entries

    def _runtime_hits(self, relative: str) -> int:
        if self.connection is None:
            return 0
        module = relative.removesuffix(".py").replace("/", "_")
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM trace_chain WHERE module LIKE ?",
            (f"%{module}%",),
        ).fetchone()
        return int(row["count"])


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets


def _classification(relative: str) -> str:
    if relative.startswith(("contracts/", "evaluation/", "observability/", "tooling/", "signal_lab/", "services/")) or relative == "worker/research_v2.py":
        return "new_v2"
    if relative in {"cli.py", "web/agent_service.py", "agent/runner.py", "agent/tools.py", "signals/pipeline.py"}:
        return "bridge_v2"
    return "reuse_v2"


__all__ = ["FileManifestEntry", "MigrationAudit"]
