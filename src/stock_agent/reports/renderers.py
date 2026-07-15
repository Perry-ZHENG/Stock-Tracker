"""Pure FinalReport renderers that never invoke a model or alter claims."""

from __future__ import annotations

import json
from typing import Literal

from stock_agent.contracts.reports import FinalReport

ReportRenderFormat = Literal["json", "markdown"]


class ReportRenderError(ValueError):
    """A caller tried to render an unvalidated or unsupported report object."""


def render_report(report: FinalReport, output_format: ReportRenderFormat) -> bytes:
    if not isinstance(report, FinalReport) or report.validation.status != "passed":
        raise ReportRenderError("only a validated FinalReport can be rendered")
    if output_format == "json":
        return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if output_format == "markdown":
        return _markdown(report).encode("utf-8")
    raise ReportRenderError(f"unsupported report format: {output_format}")


def _markdown(report: FinalReport) -> str:
    lines = ["# Research Report", "", report.draft.summary, ""]
    claims = {claim.claim_id: claim for claim in report.draft.claims}
    for section in report.draft.sections:
        lines.extend([f"## {section.title}", "", section.content, ""])
        for claim_id in section.claim_ids:
            claim = claims[claim_id]
            references = ", ".join(f"[evidence:{ref.evidence_id}](evidence://{ref.evidence_id})" for ref in claim.evidence_refs)
            lines.append(f"- {claim.text} ({claim.claim_type}; {references})")
        lines.append("")
    if report.draft.limitations:
        lines.extend(["## Limitations", "", *[f"- {item}" for item in report.draft.limitations], ""])
    lines.extend(["## Validation", "", f"- status: {report.validation.status}", f"- report_id: {report.report_id}", ""])
    return "\n".join(lines)


__all__ = ["ReportRenderError", "ReportRenderFormat", "render_report"]
