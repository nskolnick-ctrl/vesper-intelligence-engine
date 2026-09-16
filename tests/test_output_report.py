"""Tests for the VIE output layer (src/output/).

Built from hand-made PipelineOutput objects, so no data fetch or Claude call
happens. Checks what a non-technical reader sees, not the markdown internals.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.analysis.engine import OverrideThresholdBreach
from src.analysis.schema import AnalysisResult, BearCaseResult
from src.output import ReportWriteError, render_report, report_filename, write_report
from src.pipeline import PipelineOutput

REPORT_DATE = date(2026, 9, 16)


def _output(**result_overrides) -> PipelineOutput:
    result = dict(
        ticker="AAPL",
        analysis_date="2026-09-16",
        business_model_clarity=9,
        moat_score=8,
        moat_primary_source="intangible_assets",
        moat_evidence="Sustained 46% gross margin at scale.",
        financial_quality=8,
        financial_evidence="Free cash flow covers total debt roughly once.",
        primary_risk="Revenue concentration in one hardware category.",
        verdict="high_quality",
        confidence=8,
        caveats=[],
    )
    result.update(result_overrides)
    return PipelineOutput(
        data={
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "sector": "Technology",
            "price": 225.0,
            "market_cap": 3_450_000_000_000.0,
            "revenue_ttm": 391_000_000_000.0,
            "gross_margin": 0.46,
            "pe_ratio_trailing": None,
            "as_of_date": "2026-09-15",
            "source": "yfinance",
        },
        result=AnalysisResult(**result),
        bear_case=BearCaseResult(
            bear_thesis="Margin could compress.",
            key_evidence="Slowing unit growth.",
            bull_assumption_challenged="That margin reflects pricing power.",
            what_would_change_this="Margin holding through a downturn.",
        ),
        breaches=[],
        model="sonnet",
    )


def test_report_shows_plain_language_verdict_and_scores():
    report = render_report(_output(), REPORT_DATE)

    assert report.startswith("# Apple Inc. (AAPL): Research Report")
    assert "**High quality** with confidence **8 / 10**" in report
    assert "Intangible assets (brand, patents, licences)" in report
    assert "high_quality" not in report  # no raw enum values
    assert "46.0%" in report and "3.5T" in report and "391.0B" in report


def test_report_never_shows_null_as_zero_or_blank():
    report = render_report(
        _output(moat_score=None, moat_primary_source=None, moat_evidence=None), REPORT_DATE
    )

    assert "| Competitive advantage (moat) | Not assessed" in report
    assert "| Price to earnings (trailing) | Not available |" in report
    assert "None" not in report


def test_report_includes_bear_case_and_caveats():
    report = render_report(_output(caveats=["Growth figure is one year only."]), REPORT_DATE)

    assert "## The case against" in report
    assert "Margin could compress." in report
    assert "- Growth figure is one year only." in report


def test_report_flags_precommitted_threshold_breach():
    output = _output()
    output.breaches = [OverrideThresholdBreach(field="confidence", threshold=9, actual=8)]

    report = render_report(output, REPORT_DATE)

    assert "Review flag" in report
    assert "Confidence was 8, below the pre-set minimum of 9" in report


def test_report_filename_is_ticker_and_date_stamped():
    assert report_filename("aapl", REPORT_DATE) == "AAPL_2026-09-16_report.md"
    assert "/" not in report_filename("../../etc", REPORT_DATE)  # cannot escape output dir


def test_write_report_creates_directory_and_file(tmp_path: Path):
    path = write_report(_output(), tmp_path / "reports", REPORT_DATE)

    assert path.name == "AAPL_2026-09-16_report.md"
    assert "Research Report" in path.read_text(encoding="utf-8")


def test_write_report_raises_report_write_error_when_path_is_a_file(tmp_path: Path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")

    with pytest.raises(ReportWriteError, match="Could not write"):
        write_report(_output(), blocker, REPORT_DATE)
