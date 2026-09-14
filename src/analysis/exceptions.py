"""Exceptions raised by the VIE analysis layer.

Each exception names the failure mode it represents rather than being
a generic catch-all, matching the Day 5 data layer's DataFetchError /
DataValidationError pattern: nothing invalid passes downstream
silently, and whoever is debugging at midnight can tell from the
exception type alone which stage failed.
"""

from __future__ import annotations


class AnalysisAPIError(Exception):
    """Raised when the Claude API call itself fails.

    Covers network failures, timeouts, authentication errors, and
    rate limits: anything where the model never produced a response
    for this layer to work with. The original exception is chained
    (`raise ... from exc`) so the underlying cause is never lost.
    """


class ResponseParsingError(Exception):
    """Raised when the model's response is not valid JSON.

    Covers truncated output, stray prose around the JSON object, and
    the markdown-code-fence failure documented in the Day 3 prompt
    library (the AAPL test run: the prompt said "JSON only" but never
    said "no code fences by name", so the model wrapped the object in
    triple backticks and json.loads failed). This layer strips a
    single leading/trailing fence defensively before parsing, but
    still raises this error if what remains is not valid JSON, since
    silently guessing at malformed output is worse than failing loudly.
    """


class SchemaValidationError(Exception):
    """Raised when parsed JSON does not conform to the output schema.

    Covers a missing required field, a field of the wrong type, and a
    field outside its declared enum (e.g. verdict = "excellent"
    instead of one of the four permitted values). Carries enough
    context (which field, what was expected, what was received) to
    diagnose the failure without re-running the model call.
    """
