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
layer — per the Day 1 architecture note, the CLI (not yet built; Day
8) is the sole layer permitted to catch all exceptions. A pipeline
that swallowed errors here would hide exactly the failures the two
layers were built to surface.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from src.analysis import run_full_analysis
from src.analysis.engine import DEFAULT_MODEL, OverrideThresholdBreach
from src.analysis.schema import AnalysisResult
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
    client: Any | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[AnalysisResult, list[OverrideThresholdBreach]]:
    """Run the full VIE pipeline for one ticker: fetch, convert, analyse.

    Args:
        ticker: The company ticker to research.
        override_thresholds: Minimum acceptable values for integer
            result fields, fixed before this call runs (see the Day 6
            pre-commitment mechanism in src/analysis/engine.py).
        client: Anthropic-SDK-shaped client, or None for the default.
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
