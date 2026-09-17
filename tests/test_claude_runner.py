"""Tests for src/analysis/claude_runner.py.

subprocess.run and executable lookup are patched, so no test starts Claude
Code. What is under test is the contract with the CLI: the command sent, the
environment it runs in, and how each failure becomes an AnalysisAPIError.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.analysis import AnalysisAPIError, ClaudeCodeClient

FAKE_PATH = "/usr/local/bin/claude"


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _envelope(**overrides) -> str:
    body = {"type": "result", "subtype": "success", "is_error": False, "result": '{"ok": true}'}
    body.update(overrides)
    return json.dumps(body)


@pytest.fixture
def found():
    with patch("src.analysis.claude_runner.shutil.which", return_value=FAKE_PATH):
        yield


def test_complete_returns_result_text(found):
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed(_envelope())) as run:
        text = ClaudeCodeClient().complete("system", "user prompt", "sonnet")

    assert text == '{"ok": true}'
    command = run.call_args.args[0]
    assert command[:3] == [FAKE_PATH, "-p", "user prompt"]
    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--system-prompt") + 1] == "system"
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--tools") + 1] == ""  # no tools: text answer only


def test_complete_never_passes_api_credentials(found, monkeypatch):
    # The VIE must run on the user's Claude login, never an API key, even
    # if one happens to be set in the shell.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed(_envelope())) as run:
        ClaudeCodeClient().complete("s", "u", "sonnet")

    assert not any(k.startswith("ANTHROPIC_") for k in run.call_args.kwargs["env"])


def test_missing_claude_code_raises_with_install_hint():
    with patch("src.analysis.claude_runner.shutil.which", return_value=None):
        with pytest.raises(AnalysisAPIError, match="Install Claude Code"):
            ClaudeCodeClient().complete("s", "u", "sonnet")


def test_timeout_raises_analysis_api_error(found):
    with patch(
        "src.analysis.claude_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
    ):
        with pytest.raises(AnalysisAPIError, match="did not reply"):
            ClaudeCodeClient(timeout_seconds=5).complete("s", "u", "sonnet")


def test_error_envelope_raises_with_login_hint(found):
    stdout = _envelope(subtype="success", is_error=True, result="Invalid API key · Please run /login")
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed(stdout, returncode=1)):
        with pytest.raises(AnalysisAPIError, match="not logged in"):
            ClaudeCodeClient().complete("s", "u", "sonnet")


def test_non_json_output_raises(found):
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed("boom", returncode=2)):
        with pytest.raises(AnalysisAPIError, match="exited with code 2"):
            ClaudeCodeClient().complete("s", "u", "sonnet")


def test_max_turns_reached_is_an_error(found):
    stdout = _envelope(subtype="error_max_turns", result=None)
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed(stdout)):
        with pytest.raises(AnalysisAPIError, match="error_max_turns"):
            ClaudeCodeClient().complete("s", "u", "sonnet")


def test_successful_call_is_recorded_for_audit(found):
    stdout = _envelope(modelUsage={"claude-sonnet-x-20260101": {"inputTokens": 10}})
    client = ClaudeCodeClient()
    with patch("src.analysis.claude_runner.subprocess.run", return_value=_completed(stdout)):
        client.complete("s", "u", "sonnet")

    [record] = client.calls
    assert record["model_requested"] == "sonnet"
    assert record["models_used"] == ["claude-sonnet-x-20260101"]
    assert len(record["response_sha256"]) == 64
    assert record["started_at"].endswith("+00:00")
