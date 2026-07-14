"""Persistence for V2 analysis outputs, report drafts, and final reports."""

from __future__ import annotations

import json
import sqlite3

from stock_agent.contracts.analysis import AnomalyAnalysis, MacroAnalysis
from stock_agent.contracts.reports import FinalReport, ReportDraft
from stock_agent.security.redaction import redact_sensitive


class ReportRepository:
    """Store typed report artifacts without letting callers bypass validation contracts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_analysis(self, task_id: str, analysis: AnomalyAnalysis | MacroAnalysis) -> None:
        analysis_type = "anomaly" if isinstance(analysis, AnomalyAnalysis) else "macro"
        payload = analysis.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO analyses (analysis_id, task_id, analysis_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                analysis.analysis_id,
                task_id,
                analysis_type,
                _json(payload),
                payload["created_at"],
            ),
        )
        self.connection.commit()

    def get_analysis(self, analysis_id: str) -> AnomalyAnalysis | MacroAnalysis | None:
        row = self.connection.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if row is None:
            return None
        model_type = AnomalyAnalysis if row["analysis_type"] == "anomaly" else MacroAnalysis
        return model_type.model_validate_json(row["payload_json"])

    def save_draft(self, draft: ReportDraft) -> None:
        payload = draft.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO report_drafts (draft_id, task_id, payload_json, generated_at)
            VALUES (?, ?, ?, ?)
            """,
            (draft.draft_id, draft.task_id, _json(payload), payload["generated_at"]),
        )
        self.connection.commit()

    def get_draft(self, draft_id: str) -> ReportDraft | None:
        row = self.connection.execute("SELECT payload_json FROM report_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        return ReportDraft.model_validate_json(row["payload_json"]) if row is not None else None

    def save_final(self, report: FinalReport) -> None:
        """Persist only a final contract whose exact draft was previously stored."""

        stored_draft = self.get_draft(report.draft.draft_id)
        if stored_draft is None:
            raise ValueError("a FinalReport draft must be stored before finalization")
        if stored_draft != report.draft:
            raise ValueError("the stored draft does not match the FinalReport draft")
        payload = report.model_dump(mode="json")
        self.connection.execute(
            """
            INSERT INTO final_reports (report_id, draft_id, task_id, payload_json, published_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                report.report_id,
                report.draft.draft_id,
                report.draft.task_id,
                _json(payload),
                payload["published_at"],
            ),
        )
        self.connection.commit()

    def get_final(self, report_id: str) -> FinalReport | None:
        row = self.connection.execute("SELECT payload_json FROM final_reports WHERE report_id = ?", (report_id,)).fetchone()
        return FinalReport.model_validate_json(row["payload_json"]) if row is not None else None


def _json(value: object) -> str:
    return json.dumps(redact_sensitive(value), ensure_ascii=False, sort_keys=True)


__all__ = ["ReportRepository"]
