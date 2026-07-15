"""Bounded report structures used by the Report Agent.

Templates describe report organisation only.  They never add facts or make a
recommendation, which keeps content generation inside the evidence boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_agent.contracts.tasks import ReportType


@dataclass(frozen=True)
class ReportTemplate:
    """A stable section layout for one supported research report type."""

    name: ReportType
    section_titles: tuple[str, ...]
    description: str


_COUNTER_EVIDENCE = "Counter-Evidence And Unknowns"

_TEMPLATES: dict[ReportType, ReportTemplate] = {
    "facts": ReportTemplate(
        name="facts",
        section_titles=("Facts", _COUNTER_EVIDENCE),
        description="Describe registered observations and clearly record what remains unknown.",
    ),
    "anomaly": ReportTemplate(
        name="anomaly",
        section_titles=("Facts", "Anomaly Analysis", _COUNTER_EVIDENCE),
        description="Separate deterministic anomaly measurements from bounded candidate explanations.",
    ),
    "macro": ReportTemplate(
        name="macro",
        section_titles=("Facts", "Macro Analysis", _COUNTER_EVIDENCE),
        description="Present conditional transmission paths and alternative macro scenarios.",
    ),
    "signal": ReportTemplate(
        name="signal",
        section_titles=("Facts", "Signal Function Outputs", _COUNTER_EVIDENCE),
        description="Present approved signal-function observations without turning them into orders.",
    ),
    "full": ReportTemplate(
        name="full",
        section_titles=("Facts", "Signal Function Outputs", "Agent Inference", _COUNTER_EVIDENCE),
        description="Combine factual evidence, computed signals, bounded analysis, counter-evidence, and unknowns.",
    ),
}


def get_report_template(report_type: ReportType) -> ReportTemplate:
    """Return the sole supported layout for the requested report type."""

    return _TEMPLATES[report_type]


__all__ = ["ReportTemplate", "get_report_template"]
