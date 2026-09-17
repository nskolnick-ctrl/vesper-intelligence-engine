"""Claude integration for the VIE analysis layer.

Claude is reached through the user's own Claude Code login (see
claude_runner.py). No Anthropic API key is used anywhere.

Data flow (Day 1 architecture): this module receives the Day 5 data
layer's validated object, never raw external data, and returns only a
schema-validated AnalysisResult, never a raw model response. It never
imports from src.data directly — the public functions take a plain
dict (the data object's fields) so this layer depends on the shape of
the data, not on where it came from, per the Day 5 note on the data
layer's interface.

Two corrections carried over from before Day 6, both resolved in this
module:

1. Bear case verdict-downgrade trigger (see OVERRIDE_MECHANISM_NOTE
   and decide_bear_case_override below; replaced on Day 9). The Day 4 design spec said a
   downgrade triggers "when the bear case's risk score exceeds the
   bull verdict's confidence score", but the Day 3 bear case schema
   was deliberately built with no risk_score field (assigning the
   bear case a numeric severity would manufacture false precision —
   see the Day 3 prompt library's own omission note). That spec
   therefore referenced a field that was never going to exist. The
   fix is not to add the missing field, since that would undo a
   considered Day 3 decision; it is to replace the numeric comparison
   with a qualitative contradiction check that uses the fields the
   bear case schema actually has.

2. Pre-committing the evaluation override condition (see
   `override_thresholds` on run_full_analysis). Anthony's Day 4
   disagreement procedure scores a disputed verdict against five
   rubric criteria after seeing it. The gap flagged before Day 6 was
   that this decides what would justify an override only after the
   output exists. run_full_analysis's signature forces the opposite
   order: override_thresholds must be supplied by the caller before
   the Claude call is made, so the condition for overriding the tool is
   fixed before there is an output for it to be fitted to.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from typing import Any, Callable

from src.analysis.claude_runner import ClaudeClient, ClaudeCodeClient
from src.analysis.exceptions import (
    AnalysisAPIError,
    ResponseParsingError,
    SchemaValidationError,
)
from src.analysis.prompts import (
    SYSTEM_PROMPT,
    render_analysis_prompt,
    render_bear_case_prompt,
)
from src.analysis.schema import (
    AnalysisResult,
    BearCaseResult,
    validate_analysis_output,
    validate_bear_case_output,
)

# The model is chosen by alias so it follows whatever Claude models the
# user's account has, and can be overridden with VIE_MODEL without a code
# change. Claude Code does not expose a temperature setting, so the Day 1
# low-temperature choice cannot be pinned here; repeatability is instead
# enforced after the call, by strict JSON parsing and schema validation.
DEFAULT_MODEL = os.environ.get("VIE_MODEL", "sonnet")

VERDICT_ORDER = ["insufficient_data", "low_quality", "moderate_quality", "high_quality"]


def _call_claude(
    client: ClaudeClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Make one Claude call and return the raw text reply.

    Args:
        client (ClaudeClient): Object exposing ``complete(system_prompt,
            user_prompt, model)``. Passed explicitly so tests can substitute
            a fake.
        model (str): Model alias or identifier.
        system_prompt (str): The system prompt for this call.
        user_prompt (str): The rendered user prompt for this call.

    Returns:
        str: The reply text.

    Raises:
        AnalysisAPIError: If the call fails for any reason, or the reply is
            not a non-empty string.
    """
    try:
        text = client.complete(
            system_prompt=system_prompt, user_prompt=user_prompt, model=model
        )
    except AnalysisAPIError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure in the client is
        # the same failure mode from this layer's point of view.
        raise AnalysisAPIError(f"Claude call failed for model '{model}': {exc}") from exc

    if not isinstance(text, str) or not text.strip():
        raise AnalysisAPIError(f"Claude returned an empty reply for model '{model}'.")
    return text


def _strip_code_fences(text: str) -> str:
    """Defensively remove a single wrapping markdown code fence.

    The prompt (OUTPUT_SCHEMA_SPEC) already says "no markdown code
    fences" by name, which is the Day 3 fix for the AAPL run that
    came back wrapped in triple backticks. This function is a second,
    independent line of defence.

    Args:
        text (str): Raw reply text.

    Returns:
        str: The text with one wrapping fence removed, if present.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _extract_last_json_object(text: str) -> dict[str, Any] | None:
    """Find the last complete top-level JSON object inside mixed text.

    The Day 3 analysis template asks the model to write out its reasoning
    for each step before giving the JSON. Claude sometimes does exactly
    that, so the reply is prose followed by the object. This scans left to
    right, decodes every complete object it can, skips over its contents,
    and returns the last one, which is the final answer.

    Args:
        text (str): Reply text that may contain prose around the JSON.

    Returns:
        dict[str, Any] | None: The last top-level object, or None if the
        text contains no decodable object.
    """
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            last = value
        index = text.find("{", end)
    return last


def _parse_json(raw_text: str, context: str) -> dict[str, Any]:
    """Parse the model reply as JSON.

    Tries, in order: the whole reply, the reply with one wrapping code fence
    removed, then the last complete JSON object found inside the reply (for
    replies that put step-by-step reasoning before the JSON).

    Args:
        raw_text (str): Reply text from Claude.
        context (str): Name of the calling step, for the error message.

    Returns:
        dict[str, Any]: The parsed JSON value (validated by the caller).

    Raises:
        ResponseParsingError: If no JSON object can be recovered.
    """
    candidate = _strip_code_fences(raw_text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        recovered = _extract_last_json_object(raw_text)
        if recovered is not None:
            return recovered
        raise ResponseParsingError(
            f"{context}: model response contained no valid JSON object. "
            f"json error: {exc}. Raw response (truncated): {raw_text[:300]!r}"
        ) from exc


def run_analysis(
    data: dict[str, Any],
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> AnalysisResult:
    """Run the main chain-of-thought analysis and return a validated result.

    Args:
        data (dict[str, Any]): The data layer object's fields as a plain dict.
        client (ClaudeClient | None): Claude client. If None, a
            ClaudeCodeClient using the local Claude Code login is used.
        model (str): Model alias or identifier. Defaults to DEFAULT_MODEL.

    Returns:
        AnalysisResult: The validated 12-field verdict.

    Raises:
        AnalysisAPIError: If the Claude call fails.
        ResponseParsingError: If the reply is not valid JSON.
        SchemaValidationError: If the parsed JSON fails schema validation.
    """
    active_client = client if client is not None else ClaudeCodeClient()
    data_json = json.dumps(data, sort_keys=True, default=str)
    prompt = render_analysis_prompt(data_json)

    raw_text = _call_claude(active_client, model, SYSTEM_PROMPT, prompt)
    parsed = _parse_json(raw_text, context="run_analysis")
    return validate_analysis_output(parsed)


def run_bear_case(
    data: dict[str, Any],
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> BearCaseResult:
    """Run the independent bear case analysis and return a validated result.

    Deliberately takes only `data`, never an AnalysisResult: the Day 3
    rationale for this call is that it must not see the first pass's
    own output, or the bear case risks becoming a rationalisation of a
    verdict the model already committed to.

    Args:
        data (dict[str, Any]): The data layer object's fields as a plain dict.
        client (ClaudeClient | None): Claude client, or None for the default.
        model (str): Model alias or identifier.

    Returns:
        BearCaseResult: The validated 4-field bear case.

    Raises:
        AnalysisAPIError: If the Claude call fails.
        ResponseParsingError: If the reply is not valid JSON.
        SchemaValidationError: If the parsed JSON fails schema validation.
    """
    active_client = client if client is not None else ClaudeCodeClient()
    data_json = json.dumps(data, sort_keys=True, default=str)
    prompt = render_bear_case_prompt(data_json)

    raw_text = _call_claude(active_client, model, SYSTEM_PROMPT, prompt)
    parsed = _parse_json(raw_text, context="run_bear_case")
    return validate_bear_case_output(parsed)


# --- Correction 1: bear case override mechanism -----------------------------

SEVERITY_TRIGGER = 4
CONFIDENCE_CEILING = 6
# A downgrade never moves a verdict into insufficient_data: that verdict means
# "cannot judge", not "worse than low quality", so low_quality is the floor.
_DOWNGRADE_FLOOR = "low_quality"

OVERRIDE_MECHANISM_NOTE = (
    "Day 9 mechanism (replaces the Day 6 keyword-overlap trigger). The bear "
    "case call scores bear_case_severity from 1 to 5. A verdict is lowered "
    "one level when severity is 4 or more AND the main analysis's confidence "
    "is 6 or less. The confidence guard stops a moderately challenging bear "
    "case overriding a genuinely high-conviction verdict. Why the keyword "
    "trigger was removed: in the Day 9 stress test it fired for 9 of 10 "
    "companies, because any two texts about the same company share words "
    "like 'margin', 'revenue' and 'debt'. It was measuring shared vocabulary, "
    "not contradiction, and it pushed 8 of 10 verdicts to low_quality. Its "
    "downgrade note was also silently cut off whenever the caveats list was "
    "already full. The thresholds are provisional and need testing against "
    "the evaluation set."
)


@dataclass
class BearCaseOverride:
    """The record of whether the bear case lowered the verdict, and why.

    Attributes:
        applied (bool): Whether the verdict was lowered.
        original_verdict (str): The main analysis verdict before any change.
        final_verdict (str): The verdict after the rule was applied.
        severity (int | None): The bear case severity score used.
        confidence (int): The main analysis confidence used.
        reason (str): Plain-English explanation of the decision.
    """

    applied: bool
    original_verdict: str
    final_verdict: str
    severity: int | None
    confidence: int
    reason: str


def decide_bear_case_override(
    result: AnalysisResult, bear: BearCaseResult
) -> BearCaseOverride:
    """Decide whether the bear case should lower the verdict.

    Args:
        result (AnalysisResult): The validated main analysis result.
        bear (BearCaseResult): The validated, independent bear case.

    Returns:
        BearCaseOverride: The decision and the reason for it.
    """
    severity = bear.bear_case_severity
    keep = dict(
        applied=False,
        original_verdict=result.verdict,
        final_verdict=result.verdict,
        severity=severity,
        confidence=result.confidence,
    )
    if severity is None:
        return BearCaseOverride(**keep, reason="No severity score, so no override was considered.")
    if severity < SEVERITY_TRIGGER:
        return BearCaseOverride(
            **keep, reason=f"Bear case severity {severity}/5 is below the trigger of {SEVERITY_TRIGGER}."
        )
    if result.confidence > CONFIDENCE_CEILING:
        return BearCaseOverride(
            **keep,
            reason=(
                f"Bear case severity {severity}/5 met the trigger, but confidence "
                f"{result.confidence}/10 is above {CONFIDENCE_CEILING}, so the verdict stands."
            ),
        )
    index = VERDICT_ORDER.index(result.verdict)
    floor = VERDICT_ORDER.index(_DOWNGRADE_FLOOR)
    if index <= floor:
        return BearCaseOverride(
            **keep, reason=f"Verdict is already {result.verdict}, so there is no lower level to move to."
        )
    new_verdict = VERDICT_ORDER[index - 1]
    return BearCaseOverride(
        applied=True,
        original_verdict=result.verdict,
        final_verdict=new_verdict,
        severity=severity,
        confidence=result.confidence,
        reason=(
            f"Bear case severity {severity}/5 with confidence only {result.confidence}/10, "
            f"so the verdict was lowered from {result.verdict} to {new_verdict}."
        ),
    )


def apply_bear_case_override(
    result: AnalysisResult, bear: BearCaseResult
) -> AnalysisResult:
    """Return the result with the bear case override applied.

    Never mutates `result`. The decision itself is recorded separately (see
    decide_bear_case_override) rather than appended to caveats, so it can
    never be cut off by the three-caveat limit.

    Args:
        result (AnalysisResult): The validated main analysis result.
        bear (BearCaseResult): The validated, independent bear case.

    Returns:
        AnalysisResult: `result`, or a copy with the verdict lowered one level.
    """
    decision = decide_bear_case_override(result, bear)
    if not decision.applied:
        return result
    return replace(result, verdict=decision.final_verdict)


# --- Correction 2: pre-committed override thresholds ------------------------


@dataclass
class OverrideThresholdBreach:
    """One instance of the analyst's pre-committed override condition firing."""

    field: str
    threshold: int
    actual: int


def _check_override_thresholds(
    result: AnalysisResult, override_thresholds: dict[str, int] | None
) -> list[OverrideThresholdBreach]:
    """Check a result against thresholds fixed before the call was made.

    Args:
        result: The validated analysis result to check.
        override_thresholds: A mapping of integer-valued AnalysisResult
            field name (e.g. "confidence", "financial_quality",
            "moat_score") to the minimum acceptable value, decided by
            the caller before run_full_analysis was invoked — the
            signature of run_full_analysis (this argument is consumed
            before the Claude call, never after) is what makes the
            decision "pre-committed" rather than fitted to the output
            in hindsight.

    Returns:
        list[OverrideThresholdBreach]: one entry per breached field,
        empty if none breached or no thresholds were supplied.
    """
    if not override_thresholds:
        return []

    breaches = []
    result_dict = result.as_dict()
    for field_name, threshold in override_thresholds.items():
        actual = result_dict.get(field_name)
        if isinstance(actual, int) and actual < threshold:
            breaches.append(
                OverrideThresholdBreach(
                    field=field_name, threshold=threshold, actual=actual
                )
            )
    return breaches


def run_full_analysis_with_bear_case(
    data: dict[str, Any],
    override_thresholds: dict[str, int] | None = None,
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[AnalysisResult, BearCaseResult, list[OverrideThresholdBreach], BearCaseOverride]:
    """Run the full analysis and also return the bear case for reporting.

    Same pipeline as run_full_analysis. The bear case is returned as well
    so the output layer can show the reader the strongest argument against
    the verdict, not just whether it caused a downgrade.

    Args:
        data (dict[str, Any]): The data layer object's fields as a plain dict.
        override_thresholds (dict[str, int] | None): Minimum acceptable
            values for integer result fields, fixed before any call is made.
        client (ClaudeClient | None): Claude client, or None for the default.
        model (str): Model alias or identifier for both calls.

    Returns:
        tuple[AnalysisResult, BearCaseResult, list[OverrideThresholdBreach], BearCaseOverride]:
        The final result, the bear case, any threshold breaches, and the
        record of whether the bear case lowered the verdict.

    Raises:
        AnalysisAPIError: If a Claude call fails.
        ResponseParsingError: If a reply is not valid JSON.
        SchemaValidationError: If a reply fails schema validation.
    """
    active_client = client if client is not None else ClaudeCodeClient()
    bull_result = run_analysis(data, client=active_client, model=model)
    bear_result = run_bear_case(data, client=active_client, model=model)
    decision = decide_bear_case_override(bull_result, bear_result)
    final_result = apply_bear_case_override(bull_result, bear_result)
    breaches = _check_override_thresholds(final_result, override_thresholds)
    return final_result, bear_result, breaches, decision


def run_full_analysis(
    data: dict[str, Any],
    override_thresholds: dict[str, int] | None = None,
    client: ClaudeClient | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[AnalysisResult, list[OverrideThresholdBreach]]:
    """Run the full Day 6 analysis layer pipeline for one company.

    Orchestrates, in order: the main chain-of-thought analysis, the
    independent bear case, the bear-case-integration override
    (correction 1), and the pre-committed override threshold check
    (correction 2).

    Args:
        data (dict[str, Any]): The data layer object's fields as a plain dict.
        override_thresholds (dict[str, int] | None): Minimum acceptable
            values for integer result fields, fixed by the caller before this
            function runs and therefore before any output exists to fit them
            to. Pass None to skip this check.
        client (ClaudeClient | None): Claude client, or None for the default.
        model (str): Model alias or identifier for both calls.

    Returns:
        tuple[AnalysisResult, list[OverrideThresholdBreach]]: The final
        result (after any bear-case downgrade) and any pre-committed
        thresholds it breached.

    Raises:
        AnalysisAPIError: If a Claude call fails.
        ResponseParsingError: If a reply is not valid JSON.
        SchemaValidationError: If a reply fails schema validation.
    """
    final_result, _, breaches, _ = run_full_analysis_with_bear_case(
        data, override_thresholds=override_thresholds, client=client, model=model
    )
    return final_result, breaches
