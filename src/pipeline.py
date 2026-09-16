"""VIE full pipeline: Day 5 data layer -> Day 6 analysis layer.

This module is the seam the Day 6 forward note flagged as undefined:
the data layer returns a CompanyData object (the Day 2 17-field
schema), and the analysis layer's public function takes a plain dict.
Before today, that conversion had no home, which meant it risked
getting bolted on inline wherever the two layers first got wired
together. run_pipeline is that home.

Data flow (Day 1 architecture): ticker in, CompanyData out of the data
layer, dict out of company_data_to_dict, AnalysisResult out of the
analysis layer. Each stage only ever sees the previous stage's
validated output. This module does not catch exceptions from either
layer, per the Day 1 architecture note: the CLI (vie/cli.py) is the
sole layer permitted to catch all exceptions. A pipeline
that swallowed errors here would hide exactly the failures the two
layers were built to surface.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any

from src.analysis import run_full_analysis, run_full_analysis_with_bear_case
from src.analysis.claude_runner import ClaudeClient
from src.analysis.engine import DEFAULT_MODEL, OverrideThresholdBreach
from src.analysis.schema import AnalysisResult, BearCaseResult
from src.data import fetch_company_data


class PipelineConversionError(Exception):
    """Raised when the data layer's output cannot be converted to the
    dict shape the analysis layer requires.

    This is a distinct failure mode from anything either layer already
    raises: DataFetchError and DataValidationError cover the data
    layer failing to produce a valid CompanyData object in the first
    place; AnalysisAPIError, ResponseParsingError, and
    SchemaValidationError cover the analysis layer failing on a dict
    it did receive. Neither layer has a name for "the object handed
    between them was not shaped the way the seam expects" -- that is
    what this exception is for, so a break at the seam is not
    misattributed to either layer's own tests passing.
    """


def company_data_to_dict(data: Any) -> dict[str, Any]:
    """Convert the data layer's CompanyData object into a plain dict.

    Written defensively against the object's exact construction
    (dataclass, plain object with attributes, or already a dict)
    rather than assuming one, since this module does not own the Day 2
    schema's implementation and should not need to change if the data
    layer's internal representation changes -- only if the fields it
    exposes change. That matches the Day 5 note that the layer is
    "deliberately narrow at its public interface."

    Args:
        data: The object returned by fetch_company_data.

    Returns:
        dict[str, Any]: field name -> value, one entry per field the
        object exposes. No field is renamed, dropped, or defaulted;
        a missing value stays whatever the data layer set it to
        (None, per the Day 5 no-false-zero rule), it is not
        substituted here.

    Raises:
        PipelineConversionError: if `data` is not a dataclass instance,
            a plain object exposing a `__dict__`, or already a dict --
            i.e. if this function does not know how to read it.
    """
    if isinstance(data, dict):
        return dict(data)

    if dataclasses.is_dataclass(data) and not isinstance(data, type):
        return dataclasses.asdict(data)

    if hasattr(data, "__dict__"):
        return dict(vars(data))

    raise PipelineConversionError(
        f"Cannot convert data layer output of type {type(data).__name__} "
        "to a dict: it is not a dict, a dataclass instance, or a plain "
        "object exposing __dict__. The seam between src/data and "
        "src/analysis expects one of these three shapes."
    )


def run_pipeline(
    ticker: str,
    override_thresholds: dict[str, int] | None = None,
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[AnalysisResult, list[OverrideThresholdBreach]]:
    """Run the full VIE pipeline for one ticker: fetch, convert, analyse.

    Args:
        ticker: The company ticker to research.
        override_thresholds: Minimum acceptable values for integer
            result fields, fixed before this call runs (see the Day 6
            pre-commitment mechanism in src/analysis/engine.py).
        client: Claude client, or None to use the local Claude Code login.
        model: Model identifier to call.

    Returns:
        tuple[AnalysisResult, list[OverrideThresholdBreach]]: the
        final analysis result and any pre-committed threshold breaches
        it triggered.

    Raises:
        DataFetchError, DataValidationError: from the data layer, if
            the ticker cannot be fetched or the fetched data fails
            validation.
        PipelineConversionError: if the data layer's output cannot be
            converted to the dict the analysis layer requires.
        AnalysisAPIError, ResponseParsingError, SchemaValidationError:
            from the analysis layer, as run_full_analysis.
    """
    company_data = fetch_company_data(ticker)
    data_dict = company_data_to_dict(company_data)
    return run_full_analysis(
        data_dict, override_thresholds=override_thresholds, client=client, model=model
    )


@dataclass
class PipelineOutput:
    """Everything one pipeline run produced, in one object.

    Attributes:
        data (dict[str, Any]): The data layer snapshot the analysis used.
        result (AnalysisResult): The final verdict after any bear case
            downgrade.
        bear_case (BearCaseResult): The independent bear case.
        breaches (list[OverrideThresholdBreach]): Pre-committed thresholds
            the result fell below.
        model (str): Model alias or identifier used for the Claude calls.
    """

    data: dict[str, Any]
    result: AnalysisResult
    bear_case: BearCaseResult
    breaches: list[OverrideThresholdBreach] = field(default_factory=list)
    model: str = DEFAULT_MODEL

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of the whole run.

        Returns:
            dict[str, Any]: Keys analysis, bear_case, override_breaches,
            input_data and model.
        """
        return {
            "analysis": self.result.as_dict(),
            "bear_case": dataclasses.asdict(self.bear_case),
            "override_breaches": [dataclasses.asdict(b) for b in self.breaches],
            "input_data": self.data,
            "model": self.model,
        }

    def to_json(self) -> str:
        """Serialise the run to an indented JSON string.

        Returns:
            str: Valid JSON.
        """
        return json.dumps(self.as_dict(), indent=2, default=str)


def run_pipeline_full(
    ticker: str,
    override_thresholds: dict[str, int] | None = None,
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> PipelineOutput:
    """Run the pipeline and keep every intermediate the report needs.

    Args:
        ticker (str): The company ticker to research.
        override_thresholds (dict[str, int] | None): Pre-committed minimum
            values for integer result fields.
        client (ClaudeClient | None): Claude client, or None to use the local
            Claude Code login.
        model (str): Model alias or identifier.

    Returns:
        PipelineOutput: Data snapshot, verdict, bear case and breaches.

    Raises:
        DataFetchError: If the ticker cannot be fetched.
        DataValidationError: If fetched data fails validation.
        PipelineConversionError: If the data layer output cannot be converted.
        AnalysisAPIError: If a Claude call fails.
        ResponseParsingError: If a reply is not valid JSON.
        SchemaValidationError: If a reply fails schema validation.
    """
    company_data = fetch_company_data(ticker)
    data_dict = company_data_to_dict(company_data)
    result, bear_case, breaches = run_full_analysis_with_bear_case(
        data_dict, override_thresholds=override_thresholds, client=client, model=model
    )
    return PipelineOutput(
        data=data_dict, result=result, bear_case=bear_case, breaches=breaches, model=model
    )


def run_analysis(
    ticker: str,
    override_thresholds: dict[str, int] | None = None,
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Run the full pipeline for one ticker and return the result as JSON.

    This is the one-call entry point: ``run_analysis("AAPL")`` fetches the
    data, runs both Claude calls through the local Claude Code login, and
    returns validated JSON.

    Args:
        ticker (str): The company ticker to research.
        override_thresholds (dict[str, int] | None): Pre-committed minimum
            values for integer result fields.
        client (ClaudeClient | None): Claude client, or None for the default.
        model (str): Model alias or identifier.

    Returns:
        str: JSON with keys analysis, bear_case, override_breaches,
        input_data and model.

    Raises:
        DataFetchError: If the ticker cannot be fetched.
        DataValidationError: If fetched data fails validation.
        PipelineConversionError: If the data layer output cannot be converted.
        AnalysisAPIError: If a Claude call fails.
        ResponseParsingError: If a reply is not valid JSON.
        SchemaValidationError: If a reply fails schema validation.
    """
    return run_pipeline_full(
        ticker, override_thresholds=override_thresholds, client=client, model=model
    ).to_json()
