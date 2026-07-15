"""Lightweight report template exports for V2.

Runtime pipeline modules intentionally stay unimported here: ReportAgent imports
the template module, and eagerly importing ReportService would create a cycle.
"""

from stock_agent.reports.templates import ReportTemplate, get_report_template

__all__ = ["ReportTemplate", "get_report_template"]
