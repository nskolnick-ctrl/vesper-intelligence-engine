"""VIE Layer 2: Analysis.

Takes the clean data object produced by src/data/ (Day 5) and runs it
through the Claude-powered analysis pipeline specified in the Day 3-4
prompt library. Produces a schema-validated JSON result.

Public interface: run_full_analysis(). Everything else in this package
is an implementation detail the caller should not need to import
directly, matching the Day 1 principle that each layer only ever sees
the previous layer's validated output.
"""

from src.analysis.engine import run_full_analysis, run_analysis, run_bear_case
from src.analysis.schema import AnalysisResult, BearCaseResult
from src.analysis.exceptions import (
    AnalysisAPIError,
    ResponseParsingError,
    SchemaValidationError,
)

__all__ = [
    "run_full_analysis",
    "run_analysis",
    "run_bear_case",
    "AnalysisResult",
    "BearCaseResult",
    "AnalysisAPIError",
    "ResponseParsingError",
    "SchemaValidationError",
]
