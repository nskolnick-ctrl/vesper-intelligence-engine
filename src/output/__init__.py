"""VIE Layer 3: Output.

Formats a pipeline run as a markdown report for a non-technical reader.

Public interface: render_report(), write_report(), report_filename() and
ReportWriteError.
"""

from src.output.exceptions import ReportWriteError
from src.output.report import render_report, report_filename, write_report

__all__ = ["ReportWriteError", "render_report", "report_filename", "write_report"]
