"""Checks every committed output in tests/outputs/ against the schemas.

Skips cleanly when no outputs have been generated yet. Generate them with
``python3 scripts/generate_outputs.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.schema import validate_analysis_output, validate_bear_case_output

OUTPUT_FILES = sorted((Path(__file__).parent / "outputs").glob("*.json"))


@pytest.mark.skipif(not OUTPUT_FILES, reason="no outputs generated yet")
@pytest.mark.parametrize("path", OUTPUT_FILES, ids=lambda p: p.stem)
def test_saved_output_is_schema_valid(path: Path) -> None:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    validate_analysis_output(parsed["analysis"])
    validate_bear_case_output(parsed["bear_case"])
    assert parsed["analysis"]["ticker"] == path.stem
