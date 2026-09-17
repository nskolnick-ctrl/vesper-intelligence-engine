"""Tests for the command-line interface (vie/cli.py).

Covers the Day 8 checks: a valid ticker produces a dated report, an invalid
ticker gives an informative error with exit code 1 and no traceback, and
every pipeline failure type is caught. The last test is a full integration
test through the real pipeline with only the data source and Claude faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.analysis import AnalysisAPIError, SchemaValidationError
from src.analysis.schema import AnalysisResult, BearCaseResult
from src.data import DataFetchError
from src.pipeline import PipelineOutput
from vie.cli import main, parse_thresholds, validate_ticker, CliInputError

ANALYSIS = dict(
    ticker="AAPL", analysis_date="2026-09-16", business_model_clarity=9, moat_score=8,
    moat_primary_source="intangible_assets", moat_evidence="Sustained 46% gross margin.",
    financial_quality=8, financial_evidence="Strong free cash flow.",
    primary_risk="Revenue concentration.", verdict="high_quality", confidence=8, caveats=[],
)
BEAR = dict(
    bear_thesis="Margin could compress.", key_evidence="Slowing unit growth.",
    bull_assumption_challenged="That supply is diversified enough.",
    what_would_change_this="A second manufacturing region.",
)
DATA = {"ticker": "AAPL", "company_name": "Apple Inc.", "price": 225.0, "gross_margin": 0.46,
        "as_of_date": "2026-09-15", "source": "yfinance"}


def _fake_runner(ticker, override_thresholds=None, model="sonnet"):
    return PipelineOutput(
        data=DATA, result=AnalysisResult(**ANALYSIS), bear_case=BearCaseResult(**BEAR), model=model
    )


def _raising_runner(exc: Exception):
    def runner(*args, **kwargs):
        raise exc
    return runner


def test_valid_ticker_writes_dated_report(tmp_path: Path, capsys):
    code = main(["aapl", "--output-dir", str(tmp_path)], runner=_fake_runner)

    assert code == 0
    reports = list(tmp_path.glob("AAPL_*_report.md"))
    assert len(reports) == 1
    assert "Apple Inc. (AAPL)" in reports[0].read_text(encoding="utf-8")
    assert "Report written to" in capsys.readouterr().out


def test_json_flag_writes_valid_json(tmp_path: Path):
    assert main(["AAPL", "--output-dir", str(tmp_path), "--json"], runner=_fake_runner) == 0
    [json_file] = tmp_path.glob("*.json")
    assert json.loads(json_file.read_text())["analysis"]["verdict"] == "high_quality"


def test_pdf_flag_writes_pdf_next_to_report(tmp_path: Path, capsys):
    assert main(["AAPL", "--output-dir", str(tmp_path), "--pdf"], runner=_fake_runner) == 0
    [pdf_file] = tmp_path.glob("AAPL_*_report.pdf")
    assert pdf_file.read_bytes().startswith(b"%PDF")
    assert "PDF written to" in capsys.readouterr().out


def test_invalid_ticker_exits_1_without_traceback(tmp_path: Path, capsys):
    runner = MagicMock()
    code = main(["INVALID_TICKER_XYZ", "--output-dir", str(tmp_path)], runner=runner)

    err = capsys.readouterr().err
    assert code == 1
    assert "not a valid ticker" in err
    assert "Traceback" not in err
    runner.assert_not_called()


@pytest.mark.parametrize(
    "exc, expected",
    [
        (DataFetchError("no data for ZZZZ"), "Could not get data"),
        (AnalysisAPIError("Claude Code was not found"), "Claude could not complete"),
        (SchemaValidationError("missing verdict"), "did not pass validation"),
        (RuntimeError("something odd"), "Unexpected error"),
    ],
)
def test_pipeline_errors_exit_1_with_message(tmp_path: Path, capsys, exc, expected):
    code = main(["ZZZZ", "--output-dir", str(tmp_path)], runner=_raising_runner(exc))

    err = capsys.readouterr().err
    assert code == 1
    assert expected in err
    assert "Traceback" not in err
    assert not list(tmp_path.glob("*.md"))


def test_keyboard_interrupt_exits_130(tmp_path: Path, capsys):
    code = main(["AAPL", "--output-dir", str(tmp_path)], runner=_raising_runner(KeyboardInterrupt()))
    assert code == 130
    assert "Traceback" not in capsys.readouterr().err


def test_thresholds_are_parsed_and_passed_before_the_run(tmp_path: Path):
    runner = MagicMock(side_effect=_fake_runner)
    main(["AAPL", "--output-dir", str(tmp_path), "--min", "confidence=7", "--min", "moat_score=9"], runner=runner)
    assert runner.call_args.kwargs["override_thresholds"] == {"confidence": 7, "moat_score": 9}


def test_parse_thresholds_rejects_bad_input():
    with pytest.raises(CliInputError):
        parse_thresholds(["verdict=5"])
    with pytest.raises(CliInputError):
        parse_thresholds(["confidence=eleven"])
    with pytest.raises(CliInputError):
        parse_thresholds(["confidence=11"])


def test_validate_ticker_accepts_real_formats():
    assert validate_ticker(" dge.l ") == "DGE.L"
    assert validate_ticker("BRK-B") == "BRK-B"
    with pytest.raises(CliInputError):
        validate_ticker("")


def test_end_to_end_ticker_in_report_out(tmp_path: Path):
    """Full integration: real CLI, pipeline, analysis and output layers.

    Only the two external boundaries are faked: the data source and Claude.
    """
    fake_claude = MagicMock()
    fake_claude.complete.side_effect = [json.dumps(ANALYSIS), json.dumps(BEAR)]

    with patch("src.pipeline.fetch_company_data", return_value=dict(DATA)), patch(
        "src.pipeline.ClaudeCodeClient", return_value=fake_claude
    ):
        code = main(["AAPL", "--output-dir", str(tmp_path), "--json"])

    assert code == 0
    assert fake_claude.complete.call_count == 2
    [report] = tmp_path.glob("*_report.md")
    text = report.read_text(encoding="utf-8")
    assert "**High quality** with confidence **8 / 10**" in text
    assert "Margin could compress." in text
    [json_file] = tmp_path.glob("*.json")
    assert json.loads(json_file.read_text())["input_data"]["gross_margin"] == 0.46
