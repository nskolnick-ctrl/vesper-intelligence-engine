"""The Day 3-4 prompt library, reproduced exactly so the analysis
layer implements what was designed rather than a paraphrase of it.

Four pieces, per the Day 3 deliverable pack:
    1. SYSTEM_PROMPT
    2. ANALYSIS_PROMPT_TEMPLATE (the four-step chain-of-thought template)
    3. OUTPUT_SCHEMA_SPEC (the structured output specification)
    4. BEAR_CASE_PROMPT_TEMPLATE

Design rationale for each lives in "Vesper day 3 done.docx"; this
module only reproduces the text and the string-rendering functions
that fill in the data object's fields.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = (
    "You are a rigorous financial analyst evaluating public companies for "
    "a family office. You are given a single structured data object and "
    "must reason only from the fields it contains. Do not speculate beyond "
    "the provided data. Flag uncertainty explicitly rather than papering "
    "over it: a null or borderline field must lower your confidence, not "
    "be silently worked around."
)

OUTPUT_SCHEMA_SPEC = (
    "Respond with a single valid JSON object only. No markdown code "
    "fences, no preamble, no explanation outside the JSON, no trailing "
    "commentary. Conform exactly to this schema:\n\n"
    '{"ticker": string, "analysis_date": string (ISO 8601), '
    '"business_model_clarity": integer 1-10, "moat_score": integer 1-10 '
    'or null, "moat_primary_source": one of ["network_effects", '
    '"switching_costs", "intangible_assets", "cost_advantage", '
    '"efficient_scale", "none"] or null, "moat_evidence": string max 80 '
    'words or null, "financial_quality": integer 1-10, '
    '"financial_evidence": string max 60 words, "primary_risk": string '
    'max 60 words, "verdict": one of ["high_quality", "moderate_quality", '
    '"low_quality", "insufficient_data"], "confidence": integer 1-10, '
    '"caveats": array of strings max 3 items}\n\n'
    "Use null for any field the provided data does not support a "
    "conclusion on. Do not omit a field. Do not explain why a field is "
    "null; leave the reasoning for that gap out of the output entirely "
    "and let the null value speak for itself."
)

_ANALYSIS_TEMPLATE_BODY = (
    "Write out your reasoning for each step; do not skip to a conclusion.\n\n"
    "Step 1 — Business model and moat: Using sector, and any margin and "
    "revenue_growth_yoy figures available, identify which of the five moat "
    "sources (network effects, switching costs, intangible assets, cost "
    "advantage, efficient scale) the data suggests, if any. State what "
    "evidence supports this and what evidence is missing.\n\n"
    "Step 2 — Financial quality: Using gross_margin, ebit_margin, "
    "revenue_growth_yoy, free_cash_flow_ttm, total_debt, and "
    "cash_and_equivalents, assess whether the company is financially "
    "sound. Note any field that is null and what that limits you from "
    "concluding.\n\n"
    "Step 3 — Primary risk: State the single most significant risk "
    "visible in this data, not a generic industry risk. Ground it in a "
    "specific field or combination of fields.\n\n"
    "Step 4 — Synthesis: Only now, having completed steps 1-3, state your "
    "verdict and confidence. Your confidence score must be lower if you "
    "relied on any null or borderline field in steps 1-3.\n\n"
    "Produce your final answer as a JSON object matching the schema "
    "exactly. Do not include your step by step reasoning in the final "
    "JSON output, only the structured fields.\n\n"
    "Structured Output Specification (reproduced from the Prompt Library "
    "above):\n\n"
    f"{OUTPUT_SCHEMA_SPEC}\n\n"
    "Company data:\n<<<DATA_JSON>>>"
)

BEAR_CASE_PROMPT_TEMPLATE = (
    "You do not know your own prior verdict for this purpose. Treat this "
    "as a fresh analysis. Given only the same data object below, build "
    "the strongest possible case AGAINST this being a high-quality "
    "investment. You are not being asked for a balanced view: construct "
    "the most rigorous bear case the data can support.\n\n"
    "Address: what specific field or combination of fields is most "
    "concerning; what a bull case would have to be assuming that the "
    "data does not support; and one thing that would have to be true, "
    "which is not currently visible in this data, for the bear case to "
    "be wrong.\n\n"
    "Do not assign a numeric probability or severity score to this case. "
    "State it as a reasoned argument only.\n\n"
    "Respond with JSON: "
    '{"bear_thesis": string max 60 words, "key_evidence": string max 60 '
    'words, "bull_assumption_challenged": string max 60 words, '
    '"what_would_change_this": string max 40 words}\n\n'
    "Company data:\n<<<DATA_JSON>>>"
)


def render_analysis_prompt(data_json: str) -> str:
    """Fill the Day 3 chain-of-thought template with a data object.

    Args:
        data_json: The Day 5 data layer object, already serialised to
            a JSON string by the caller (engine.py), so this module
            has no dependency on the data layer's internal type.

    Returns:
        str: the complete user-turn prompt to send with SYSTEM_PROMPT.
    """
    # Plain substitution, not str.format: both templates embed literal
    # JSON schema examples full of curly braces, which .format would
    # misparse as placeholders.
    return _ANALYSIS_TEMPLATE_BODY.replace("<<<DATA_JSON>>>", data_json)


def render_bear_case_prompt(data_json: str) -> str:
    """Fill the Day 3 bear case template with a data object.

    Args:
        data_json: The same serialised data object passed to
            render_analysis_prompt. The bear case call is independent
            of the main analysis call by design (Day 3 rationale: "this
            runs as a separate call that only sees the data object,
            never the first pass's own output, so the bear case cannot
            become a rationalization of a verdict the model already
            committed to"), so this function accepts only the data,
            never an AnalysisResult.

    Returns:
        str: the complete user-turn prompt to send with SYSTEM_PROMPT.
    """
    return BEAR_CASE_PROMPT_TEMPLATE.replace("<<<DATA_JSON>>>", data_json)
