"""Retention planning for lake data and cached news."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

RetentionAction = Literal["keep", "delete_temp", "compress_news"]


@dataclass(frozen=True)
class RetentionPlanItem:
    path: Path
    action: RetentionAction
    reason: str
    dataset: str


@dataclass(frozen=True)
class RetentionPlan:
    dry_run: bool
    root: Path
    items: list[RetentionPlanItem] = field(default_factory=list)
    executed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def affected_count(self) -> int:
        return len([item for item in self.items if item.action != "keep"])


def build_retention_plan(
    lake_root: Path,
    *,
    today: date | None = None,
    temp_retention_days: int = 1,
    news_compress_after_days: int = 7,
) -> RetentionPlan:
    active_today = today or date.today()
    items: list[RetentionPlanItem] = []
    if not lake_root.exists():
        return RetentionPlan(dry_run=True, root=lake_root, items=[])

    temp_cutoff = active_today - timedelta(days=temp_retention_days)
    news_cutoff = active_today - timedelta(days=news_compress_after_days)
    for path in sorted(lake_root.rglob("*")):
        if not path.is_file():
            continue
        dataset = _dataset_name(lake_root, path)
        partition_date = _partition_date(path)
        action: RetentionAction = "keep"
        reason = "within retention or required for trace/statistics"
        if dataset in {"raw_bars", "features"} and partition_date is not None and partition_date < temp_cutoff:
            action = "delete_temp"
            reason = f"{dataset} older than {temp_retention_days} day(s); source ids and trace remain in SQLite"
        elif dataset == "news" and partition_date is not None and partition_date < news_cutoff:
            action = "compress_news"
            reason = f"news older than {news_compress_after_days} day(s); retain title/summary/url"
        items.append(RetentionPlanItem(path=path, action=action, reason=reason, dataset=dataset))
    return RetentionPlan(dry_run=True, root=lake_root, items=items)


def execute_retention_plan(plan: RetentionPlan, *, execute: bool = False) -> RetentionPlan:
    if not execute:
        return plan
    errors: list[str] = []
    for item in plan.items:
        if item.action == "keep":
            continue
        try:
            if item.action == "compress_news":
                _compress_news_file(item.path)
            elif item.action == "delete_temp":
                item.path.unlink()
        except OSError as exc:
            errors.append(f"{item.path}: {exc}")
    return RetentionPlan(dry_run=False, root=plan.root, items=plan.items, executed=True, errors=errors)


def format_retention_plan(plan: RetentionPlan) -> str:
    lines = [
        "retention_status=ok" if not plan.errors else "retention_status=partial",
        f"dry_run={str(plan.dry_run).lower()}",
        f"executed={str(plan.executed).lower()}",
        f"affected_count={plan.affected_count}",
        "action | dataset | path | reason",
    ]
    for item in plan.items:
        if item.action == "keep":
            continue
        lines.append(" | ".join([item.action, item.dataset, str(item.path), item.reason]))
    if plan.errors:
        lines.append("errors:")
        lines.extend(f"- {error}" for error in plan.errors)
    return "\n".join(lines) + "\n"


def _dataset_name(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return "unknown"


def _partition_date(path: Path) -> date | None:
    for part in path.parts:
        if part.startswith("date="):
            try:
                return datetime.fromisoformat(part.removeprefix("date=")).date()
            except ValueError:
                return None
    return None


def _compress_news_file(path: Path) -> None:
    lines = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        # Keep a compact text form without needing a JSON dependency at call sites.
        lines.append(raw_line)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


__all__ = ["RetentionPlan", "RetentionPlanItem", "build_retention_plan", "execute_retention_plan", "format_retention_plan"]
