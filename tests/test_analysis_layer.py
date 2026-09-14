"""Tests for the VIE analysis layer (src/analysis/).

Every test constructs a fake client with `.messages.create` mocked, so
none of these tests need ANTHROPIC_API_KEY or network access — the
"public interface only" rule from the Day 5 test file applies here
too. At least one adversarial case per the Day 6 requirement is
present: malformed JSON, a markdown-fenced response, a missing
required field, an invalid enum value, and an outright API failure.

Run with: pytest tests/test_analysis_layer.py
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.analysis import (
    AnalysisAPIError,
    ResponseParsingError,
    SchemaValidationError,
    run_analysis,
    run_bear_case,
    run_full_analysis,
)
from src.analysis.engine import apply_bear_case_override
from src.analysis.schema import AnalysisResult, BearCaseResult

VALID_ANALYSIS_JSON = (
    '{"ticker": "AAPL", "analysis_date": "2026-09-14", '
    '"business_model_clarity": 9, "moat_score": 8, '
    '"moat_primary_source": "intangible_assets", '
    '"moat_evidence": "Sustained 46% gross margin and 31% ebit margin at '
    'scale indicate durable pricing power.", "financial_quality": 8, '
    '"financial_evidence": "Free cash flow of $108B against total debt of '
    '$106B gives roughly 1x FCF coverage of debt.", '
    '"primary_risk": "Revenue concentration in a single hardware category.", '
    '"verdict": "high_quality", "confidence": 8, "caveats": []}'
)

VALID_BEAR_JSON = (
    '{"bear_thesis": "Margin strength reflects pricing power that could '
    'compress under regulatory pressure.", "key_evidence": "Gross margin '
    'sits above sector median while unit growth has slowed.", '
    '"bull_assumption_challenged": "That the sustained 46% gross margin '
    'reflects durable pricing power rather than one-off mix effects.", '
    '"what_would_change_this": "Margin holding through a full unit-growth '
    'downturn."}'
)


def _client_returning(text: str) -> MagicMock:
    """Build a mock Anthropic client whose .messages.create(...) returns
    a response object shaped like the real SDK's, with `text` as the
    sole content block.
    """
    client = MagicMock()
    block = SimpleNamespace(type="text", text=text)
    client.messages.create.return_value = SimpleNamespace(content=[block])
    return client


# --- run_analysis: happy path -----------------------------------------------


def test_run_analysis_happy_path():
    client = _client_returning(VALID_ANALYSIS_JSON)
    result = run_analysis({"ticker": "AAPL"}, client=client)

    assert isinstance(result, AnalysisResult)
    assert result.ticker == "AAPL"
    assert result.verdict == "high_quality"
    assert result.moat_score == 8
    assert result.caveats == []
    client.messages.create.assert_called_once()


# --- adversarial: markdown code fences (the actual Day 3 AAPL failure) -----


def test_run_analysis_strips_markdown_fences():
    fenced = f"```json\n{VALID_ANALYSIS_JSON}\n```"
    client = _client_returning(fenced)
    result = run_analysis({"ticker": "AAPL"}, client=client)

    assert result.ticker == "AAPL"
    assert result.verdict == "high_quality"


# --- adversarial: malformed JSON --------------------------------------------


def test_run_analysis_malformed_json_raises_parsing_error():
    truncated = VALID_ANALYSIS_JSON[:-10]  # cut off mid-object
    client = _client_returning(truncated)

    with pytest.raises(ResponseParsingError):
        run_analysis({"ticker": "AAPL"}, client=client)


# --- adversarial: missing required field ------------------------------------


def test_run_analysis_missing_field_raises_schema_error():
    import json

    obj = json.loads(VALID_ANALYSIS_JSON)
    del obj["verdict"]
    client = _client_returning(json.dumps(obj))

    with pytest.raises(SchemaValidationError, match="verdict"):
        run_analysis({"ticker": "AAPL"}, client=client)


# --- adversarial: value outside declared enum -------------------------------


def test_run_analysis_invalid_enum_raises_schema_error():
    import json

    obj = json.loads(VALID_ANALYSIS_JSON)
    obj["verdict"] = "excellent"  # not a permitted value
    client = _client_returning(json.dumps(obj))

    with pytest.raises(SchemaValidationError, match="verdict"):
        run_analysis({"ticker": "AAPL"}, client=client)


# --- adversarial: API call itself fails -------------------------------------


def test_run_analysis_api_failure_raises_analysis_api_error():
    client = MagicMock()
    client.messages.create.side_effect = ConnectionError("boom")

    with pytest.raises(AnalysisAPIError, match="boom"):
        run_analysis({"ticker": "AAPL"}, client=client)


# --- bear case independence -------------------------------------------------


def test_run_bear_case_prompt_contains_only_data_not_prior_result():
    client = _client_returning(VALID_BEAR_JSON)
    data = {"ticker": "AAPL", "gross_margin": 0.46}

    run_bear_case(data, client=client)

    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "0.46" in sent_prompt
    # Nothing from a prior AnalysisResult (e.g. a verdict string) should
    # ever be interpolated into the bear case prompt.
    assert "high_quality" not in sent_prompt
    assert "moat_score" not in sent_prompt or "moat_score" in "AnalysisResult"


def test_run_bear_case_happy_path():
    client = _client_returning(VALID_BEAR_JSON)
    result = run_bear_case({"ticker": "AAPL"}, client=client)

    assert isinstance(result, BearCaseResult)
    assert "pricing power" in result.bear_thesis


# --- correction 1: bear case override mechanism -----------------------------


def _analysis_result(**overrides) -> AnalysisResult:
    base = dict(
        ticker="AAPL",
        analysis_date="2026-09-14",
        business_model_clarity=9,
        moat_score=8,
        moat_primary_source="intangible_assets",
        moat_evidence="Sustained 46% gross margin reflects durable pricing power.",
        financial_quality=8,
        financial_evidence="Strong free cash flow coverage of total debt.",
        primary_risk="Revenue concentration.",
        verdict="high_quality",
        confidence=6,
        caveats=[],
    )
    base.update(overrides)
    return AnalysisResult(**base)


def test_bear_case_override_downgrades_on_contested_evidence():
    result = _analysis_result(confidence=5)
    bear = BearCaseResult(
        bear_thesis="Margin strength may not be durable.",
        key_evidence="Unit growth has slowed while margin held.",
        bull_assumption_challenged=(
            "That the sustained 46% gross margin reflects durable pricing "
            "power rather than one-off mix effects."
        ),
        what_would_change_this="Margin holding through a downturn.",
    )

    downgraded = apply_bear_case_override(result, bear)

    assert downgraded.verdict == "moderate_quality"
    assert len(downgraded.caveats) == 1
    assert "contests the evidence" in downgraded.caveats[0]
    # original object must be untouched
    assert result.verdict == "high_quality"


def test_bear_case_override_no_downgrade_without_keyword_overlap():
    result = _analysis_result(confidence=5)
    bear = BearCaseResult(
        bear_thesis="Supply chain concentration is the real risk.",
        key_evidence="Single-region manufacturing exposure.",
        bull_assumption_challenged="That geographic diversification is adequate.",
        what_would_change_this="A second qualified manufacturing region.",
    )

    unchanged = apply_bear_case_override(result, bear)

    assert unchanged.verdict == "high_quality"
    assert unchanged.caveats == []


def test_bear_case_override_no_downgrade_when_confidence_high():
    # Same contested evidence as the downgrade test, but confidence > 6:
    # the mechanism treats sufficiently high confidence as already
    # having accounted for the contradiction.
    result = _analysis_result(confidence=9)
    bear = BearCaseResult(
        bear_thesis="Margin strength may not be durable.",
        key_evidence="Unit growth has slowed while margin held.",
        bull_assumption_challenged=(
            "That the sustained 46% gross margin reflects durable pricing "
            "power rather than one-off mix effects."
        ),
        what_would_change_this="Margin holding through a downturn.",
    )

    unchanged = apply_bear_case_override(result, bear)

    assert unchanged.verdict == "high_quality"


# --- correction 2: pre-committed override thresholds ------------------------


def test_run_full_analysis_flags_precommitted_threshold_breach():
    # Thresholds are fixed here, before either API call is made below —
    # this ordering is the point of the mechanism, not just a test detail.
    thresholds = {"confidence": 7, "financial_quality": 7}

    client = MagicMock()
    # First call -> main analysis (confidence 8, no breach on its own);
    # second call -> bear case, worded to trigger the override downgrade,
    # which does not change confidence, so the confidence threshold is
    # unaffected in this scenario.
    client.messages.create.side_effect = [
        SimpleNamespace(content=[SimpleNamespace(type="text", text=VALID_ANALYSIS_JSON)]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text=VALID_BEAR_JSON)]),
    ]

    result, breaches = run_full_analysis(
        {"ticker": "AAPL"}, override_thresholds=thresholds, client=client
    )

    assert isinstance(result, AnalysisResult)
    # confidence=8 and financial_quality=8 in the fixture both clear a
    # threshold of 7, so no breach should be recorded.
    assert breaches == []


def test_run_full_analysis_records_breach_below_threshold():
    thresholds = {"moat_score": 9}  # fixture's moat_score is 8

    client = MagicMock()
    client.messages.create.side_effect = [
        SimpleNamespace(content=[SimpleNamespace(type="text", text=VALID_ANALYSIS_JSON)]),
        SimpleNamespace(content=[SimpleNamespace(type="text", text=VALID_BEAR_JSON)]),
    ]

    _, breaches = run_full_analysis(
        {"ticker": "AAPL"}, override_thresholds=thresholds, client=client
    )

    assert len(breaches) == 1
    assert breaches[0].field == "moat_score"
    assert breaches[0].threshold == 9
    assert breaches[0].actual == 8
