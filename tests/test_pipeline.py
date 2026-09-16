"""Integration tests for src/pipeline.py: the seam between the Day 5
data layer and the Day 6 analysis layer.

These are integration tests, not unit tests of either layer: both
layers are mocked at their public boundary (fetch_company_data, and
the Claude client passed into the analysis layer), and what's under
test is the conversion and wiring between them, not either layer's
own internal logic (that's covered in test_data_layer.py and
test_analysis_layer.py respectively).

Run with: pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.pipeline import PipelineConversionError, company_data_to_dict, run_pipeline

# --- Fixtures ----------------------------------------------------------------

VALID_ANALYSIS_JSON = (
    '{"ticker": "AAPL", "analysis_date": "2026-09-15", '
    '"business_model_clarity": 9, "moat_score": 8, '
    '"moat_primary_source": "intangible_assets", '
    '"moat_evidence": "Sustained 46% gross margin at scale.", '
    '"financial_quality": 8, "financial_evidence": "Strong FCF coverage.", '
    '"primary_risk": "Revenue concentration.", "verdict": "high_quality", '
    '"confidence": 8, "caveats": []}'
)

VALID_BEAR_JSON = (
    '{"bear_thesis": "Margin could compress.", "key_evidence": "Slowing '
    'unit growth.", "bull_assumption_challenged": "That margin reflects '
    'durable pricing power.", "what_would_change_this": "A downturn test."}'
)


@dataclasses.dataclass
class FakeCompanyData:
    """Stand-in for the Day 2 17-field CompanyData object, for testing
    the pipeline's conversion step without importing the real data
    layer's class (which this test suite does not own).
    """

    ticker: str
    company_name: str
    sector: str | None
    price: float
    market_cap: float | None
    fifty_two_week_low: float | None
    fifty_two_week_high: float | None
    revenue_ttm: float | None
    revenue_growth_yoy: float | None
    gross_margin: float | None
    ebit_margin: float | None
    total_debt: float | None
    cash_and_equivalents: float | None
    free_cash_flow_ttm: float | None
    pe_ratio_trailing: float | None
    as_of_date: str
    source: str


def _fake_company_data(**overrides) -> FakeCompanyData:
    base = dict(
        ticker="AAPL",
        company_name="Apple Inc.",
        sector="Technology",
        price=225.0,
        market_cap=3_450_000_000_000.0,
        fifty_two_week_low=164.08,
        fifty_two_week_high=237.49,
        revenue_ttm=391_000_000_000.0,
        revenue_growth_yoy=0.02,
        gross_margin=0.46,
        ebit_margin=0.31,
        total_debt=106_000_000_000.0,
        cash_and_equivalents=65_000_000_000.0,
        free_cash_flow_ttm=108_000_000_000.0,
        pe_ratio_trailing=None,
        as_of_date="2026-09-15",
        source="yfinance",
    )
    base.update(overrides)
    return FakeCompanyData(**base)


def _client_with_responses(*texts: str) -> MagicMock:
    """Fake Claude client returning `texts` in order across successive
    .complete(...) calls (main analysis call, then bear case call).
    """
    client = MagicMock()
    client.complete.side_effect = [
        t for t in texts
    ]
    return client


# --- company_data_to_dict ----------------------------------------------------


def test_company_data_to_dict_preserves_all_fields():
    data = _fake_company_data()
    result = company_data_to_dict(data)

    expected_fields = {f.name for f in dataclasses.fields(FakeCompanyData)}
    assert set(result.keys()) == expected_fields
    assert result["ticker"] == "AAPL"
    assert result["gross_margin"] == 0.46


def test_company_data_to_dict_preserves_none_values():
    # The Day 5 no-false-zero rule: a None field must stay None through
    # the conversion, never become 0 or get dropped.
    data = _fake_company_data(pe_ratio_trailing=None, sector=None)
    result = company_data_to_dict(data)

    assert result["pe_ratio_trailing"] is None
    assert result["sector"] is None
    assert "pe_ratio_trailing" in result  # present, not omitted


def test_company_data_to_dict_handles_plain_object():
    # Fallback path: an object that is not a dataclass but exposes
    # attributes directly (e.g. a simple namespace-style object).
    plain = SimpleNamespace(ticker="MSFT", gross_margin=0.68)
    result = company_data_to_dict(plain)

    assert result == {"ticker": "MSFT", "gross_margin": 0.68}


def test_company_data_to_dict_handles_dict_passthrough():
    already_a_dict = {"ticker": "GOOGL", "gross_margin": 0.58}
    result = company_data_to_dict(already_a_dict)

    assert result == already_a_dict
    assert result is not already_a_dict  # returns a copy, not the same object


def test_company_data_to_dict_rejects_unrecognised_shape():
    with pytest.raises(PipelineConversionError, match="Cannot convert"):
        company_data_to_dict(object())


# --- run_pipeline: happy path and error propagation -------------------------


def test_run_pipeline_happy_path():
    client = _client_with_responses(VALID_ANALYSIS_JSON, VALID_BEAR_JSON)

    with patch("src.pipeline.fetch_company_data", return_value=_fake_company_data()):
        result, breaches = run_pipeline("AAPL", client=client)

    assert result.ticker == "AAPL"
    assert result.verdict == "high_quality"
    assert breaches == []
    # Confirm the data actually crossed the seam: the rendered prompt
    # sent to the mocked client should contain a real field value from
    # the fake CompanyData, not a placeholder.
    sent_prompt = client.complete.call_args_list[0].kwargs["user_prompt"]
    assert "0.46" in sent_prompt  # gross_margin


def test_run_pipeline_propagates_data_fetch_error():
    class FakeDataFetchError(Exception):
        pass

    with patch(
        "src.pipeline.fetch_company_data", side_effect=FakeDataFetchError("bad ticker")
    ):
        with pytest.raises(FakeDataFetchError, match="bad ticker"):
            run_pipeline("NOTATICKER")


def test_run_pipeline_propagates_conversion_error():
    # A data layer that returns something this pipeline doesn't know
    # how to read should surface PipelineConversionError, not a
    # confusing failure inside the analysis layer.
    with patch("src.pipeline.fetch_company_data", return_value=object()):
        with pytest.raises(PipelineConversionError):
            run_pipeline("AAPL")


def test_run_pipeline_propagates_analysis_schema_error():
    import json

    from src.analysis.exceptions import SchemaValidationError

    bad_json = json.dumps({**json.loads(VALID_ANALYSIS_JSON), "verdict": "excellent"})
    client = _client_with_responses(bad_json, VALID_BEAR_JSON)

    with patch("src.pipeline.fetch_company_data", return_value=_fake_company_data()):
        with pytest.raises(SchemaValidationError):
            run_pipeline("AAPL", client=client)


def test_run_pipeline_forwards_override_thresholds():
    client = _client_with_responses(VALID_ANALYSIS_JSON, VALID_BEAR_JSON)

    with patch("src.pipeline.fetch_company_data", return_value=_fake_company_data()):
        _, breaches = run_pipeline(
            "AAPL", override_thresholds={"moat_score": 9}, client=client
        )

    assert len(breaches) == 1
    assert breaches[0].field == "moat_score"
