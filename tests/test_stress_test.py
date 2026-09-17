"""Tests for scripts/stress_test.py, using a fake pipeline so nothing hits the network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.analysis.schema import AnalysisResult, BearCaseResult
from src.data import DataFetchError
from src.pipeline import PipelineOutput

_SPEC = importlib.util.spec_from_file_location(
    "stress_test", Path(__file__).resolve().parent.parent / "scripts" / "stress_test.py"
)
stress_test = importlib.util.module_from_spec(_SPEC)
sys.modules["stress_test"] = stress_test  # dataclasses need the module registered
_SPEC.loader.exec_module(stress_test)


def _fake_output(ticker: str) -> PipelineOutput:
    return PipelineOutput(
        data={"ticker": ticker, "company_name": "Example", "price": 10.0, "pe_ratio_trailing": None},
        result=AnalysisResult(
            ticker=ticker, analysis_date="2026-09-17", business_model_clarity=6, moat_score=None,
            moat_primary_source=None, moat_evidence=None, financial_quality=4,
            financial_evidence="Thin margins.", primary_risk="Losses.", verdict="low_quality",
            confidence=3, caveats=[],
        ),
        bear_case=BearCaseResult("Burning cash.", "Negative margin.", "That losses narrow.", "Profit.", 4),
    )


def test_one_failure_does_not_stop_the_run_and_gate_is_reported(tmp_path: Path):
    cases = stress_test.DEFAULT_CASES[:3]

    def runner(ticker, **kwargs):
        if ticker == cases[1].ticker:
            raise DataFetchError("no data")
        return _fake_output(ticker)

    results = [stress_test.run_case(c, runner=runner, output_dir=tmp_path) for c in cases]
    md_path = stress_test.write_results(results, output_dir=tmp_path)

    assert [r.succeeded for r in results] == [True, False, True]
    assert "DataFetchError: no data" in results[1].error
    assert results[0].notes["missing_inputs"] == ["pe_ratio_trailing"]
    text = md_path.read_text()
    assert "2 of 3 companies succeeded" in text
    assert "Moat evidence (1-5)" in text
    assert list(tmp_path.glob("*.csv"))
