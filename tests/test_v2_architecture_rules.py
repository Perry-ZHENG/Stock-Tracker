from __future__ import annotations

import ast
from pathlib import Path

from stock_agent.evaluation.migration_audit import MigrationAudit


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "stock_agent"


def test_v2_core_never_imports_broker_or_legacy_react_agent() -> None:
    protected = [SOURCE / "agents", SOURCE / "services", SOURCE / "tooling", SOURCE / "signal_lab", SOURCE / "evaluation"]
    forbidden = {"stock_agent.broker", "stock_agent.agent"}
    imports = {
        node.module
        for directory in protected
            for path in directory.rglob("*.py")
            if path.name != "legacy.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(value == item or value.startswith(f"{item}.") for value in imports for item in forbidden)


def test_manifest_marks_bridges_for_review_instead_of_declaring_them_deleted() -> None:
    manifest = MigrationAudit(ROOT).build_manifest()
    by_path = {entry.path: entry for entry in manifest}

    assert by_path["web/agent_service.py"].classification == "bridge_v2"
    assert by_path["services/agent_service.py"].classification == "new_v2"
    assert by_path["broker/base.py"].classification == "reuse_v2"
    assert all(entry.decision != "eligible_for_review" or not entry.importers for entry in manifest)
