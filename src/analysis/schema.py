"""Output schema for the VIE analysis layer.

Two distinct, independently-validated schemas live here:

- AnalysisResult: the 12-field main verdict schema from the Day 3
  Structured Output Specification. This is the "analysis output
  schema" referenced in the Day 4 canonical-schema-reconciliation
  note, deliberately distinct from the 17-field Day 2 data layer
  schema in claude/vie-data-layer-schema.md.
- BearCaseResult: the 4-field bear case schema from the Day 3 bear
  case prompt. It has no numeric field: the Day 3 design note is
  explicit that a probability or severity score on the bear case
  would manufacture false precision, so the schema only ever holds
  reasoned text.

That second point is the source of the correction due before Day 6.
The Day 4 "bear case integration design spec" said a verdict downgrade
should trigger "when the bear case's risk score exceeds the bull
verdict's confidence score" — but BearCaseResult has no risk_score
field and never will, by the Day 3 design decision above. See
`OVERRIDE_MECHANISM_NOTE` in engine.py for the corrected mechanism
that replaces the numeric comparison the original spec assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.analysis.exceptions import SchemaValidationError

# --- Day 3 Structured Output Specification (12 fields) ---------------------

MOAT_SOURCES = frozenset(
    {
        "network_effects",
        "switching_costs",
        "intangible_assets",
        "cost_advantage",
        "efficient_scale",
        "none",
    }
)

VERDICTS = frozenset(
    {"high_quality", "moderate_quality", "low_quality", "insufficient_data"}
)

# field -> (python types allowed, nullable, enum or None)
ANALYSIS_FIELDS: dict[str, tuple[tuple[type, ...], bool, frozenset[str] | None]] = {
    "ticker": ((str,), False, None),
    "analysis_date": ((str,), False, None),
    "business_model_clarity": ((int,), False, None),
    "moat_score": ((int,), True, None),
    "moat_primary_source": ((str,), True, MOAT_SOURCES),
    "moat_evidence": ((str,), True, None),
    "financial_quality": ((int,), False, None),
    "financial_evidence": ((str,), False, None),
    "primary_risk": ((str,), False, None),
    "verdict": ((str,), False, VERDICTS),
    "confidence": ((int,), False, None),
    "caveats": ((list,), False, None),
}

# --- Day 3 Bear Case Prompt schema (4 fields, no numeric score) ------------

BEAR_CASE_FIELDS: dict[str, tuple[tuple[type, ...], bool, frozenset[str] | None]] = {
    "bear_thesis": ((str,), False, None),
    "key_evidence": ((str,), False, None),
    "bull_assumption_challenged": ((str,), False, None),
    "what_would_change_this": ((str,), False, None),
}


@dataclass
class AnalysisResult:
    """The validated 12-field main verdict, as a typed object.

    Constructed only by `validate_analysis_output`; never built
    directly from a raw model response, per the Day 6 critical design
    requirement that raw responses never reach the caller unvalidated.
    """

    ticker: str
    analysis_date: str
    business_model_clarity: int
    moat_score: int | None
    moat_primary_source: str | None
    moat_evidence: str | None
    financial_quality: int
    financial_evidence: str
    primary_risk: str
    verdict: str
    confidence: int
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the result as a plain, JSON-serialisable dict.

        Returns:
            dict[str, Any]: One entry per schema field.
        """
        return {
            "ticker": self.ticker,
            "analysis_date": self.analysis_date,
            "business_model_clarity": self.business_model_clarity,
            "moat_score": self.moat_score,
            "moat_primary_source": self.moat_primary_source,
            "moat_evidence": self.moat_evidence,
            "financial_quality": self.financial_quality,
            "financial_evidence": self.financial_evidence,
            "primary_risk": self.primary_risk,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "caveats": list(self.caveats),
        }


@dataclass
class BearCaseResult:
    """The validated 4-field bear case, as a typed object.

    Deliberately has no severity or probability field: see the module
    docstring above.
    """

    bear_thesis: str
    key_evidence: str
    bull_assumption_challenged: str
    what_would_change_this: str


def _validate_against_spec(
    obj: dict[str, Any],
    spec: dict[str, tuple[tuple[type, ...], bool, frozenset[str] | None]],
    schema_name: str,
) -> None:
    """Shared validation routine for both schemas.

    Args:
        obj (dict[str, Any]): Parsed JSON to validate.
        spec (dict): Field name to (allowed types, nullable, enum or None).
        schema_name (str): Schema name used in error messages.

    Returns:
        None

    Raises:
        SchemaValidationError: On the first problem found, naming the field,
            what was expected, and what was received.
    """
    if not isinstance(obj, dict):
        raise SchemaValidationError(
            f"{schema_name}: expected a JSON object at the top level, got "
            f"{type(obj).__name__}."
        )

    for field_name, (allowed_types, nullable, enum) in spec.items():
        if field_name not in obj:
            raise SchemaValidationError(
                f"{schema_name}: missing required field '{field_name}'. "
                f"Every field must be present; use null rather than omitting "
                f"a field the data does not support a conclusion on."
            )

        value = obj[field_name]

        if value is None:
            if nullable:
                continue
            raise SchemaValidationError(
                f"{schema_name}: field '{field_name}' is null but is not "
                f"nullable in this schema."
            )

        if not isinstance(value, allowed_types):
            expected = " or ".join(t.__name__ for t in allowed_types)
            raise SchemaValidationError(
                f"{schema_name}: field '{field_name}' expected type "
                f"{expected}, got {type(value).__name__} ({value!r})."
            )

        # bool is a subclass of int in Python; reject it explicitly so a
        # stray true/false does not silently pass an int-typed field.
        if int in allowed_types and isinstance(value, bool):
            raise SchemaValidationError(
                f"{schema_name}: field '{field_name}' expected int, got bool."
            )

        if enum is not None and value not in enum:
            raise SchemaValidationError(
                f"{schema_name}: field '{field_name}' value {value!r} is not "
                f"one of the permitted values {sorted(enum)}."
            )

    if schema_name == "AnalysisResult":
        caveats = obj["caveats"]
        if not all(isinstance(item, str) for item in caveats):
            raise SchemaValidationError(
                "AnalysisResult: field 'caveats' must be a list of strings."
            )
        if len(caveats) > 3:
            raise SchemaValidationError(
                "AnalysisResult: field 'caveats' must have at most 3 items, "
                f"got {len(caveats)}."
            )


def validate_analysis_output(obj: dict[str, Any]) -> AnalysisResult:
    """Validate a parsed JSON object against the 12-field schema.

    Args:
        obj: The parsed (but not yet validated) JSON response body.

    Returns:
        AnalysisResult: the validated, typed result.

    Raises:
        SchemaValidationError: if any field is missing, of the wrong
            type, or outside its declared enum.
    """
    _validate_against_spec(obj, ANALYSIS_FIELDS, "AnalysisResult")
    return AnalysisResult(**{k: obj[k] for k in ANALYSIS_FIELDS})


def validate_bear_case_output(obj: dict[str, Any]) -> BearCaseResult:
    """Validate a parsed JSON object against the 4-field bear case schema.

    Args:
        obj: The parsed (but not yet validated) JSON response body.

    Returns:
        BearCaseResult: the validated, typed result.

    Raises:
        SchemaValidationError: if any field is missing or of the
            wrong type.
    """
    _validate_against_spec(obj, BEAR_CASE_FIELDS, "BearCaseResult")
    return BearCaseResult(**{k: obj[k] for k in BEAR_CASE_FIELDS})
