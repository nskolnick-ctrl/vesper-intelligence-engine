"""Runs Claude through the user's own Claude Code login.

The VIE does not call the Anthropic API and needs no API key. Every model
call goes through the Claude Code command line tool in headless mode
(``claude -p``), which runs on whichever Claude account is logged in on the
machine running the VIE. Whoever runs the engine uses their own Claude.

Setup for a new machine is two steps: install Claude Code, then run
``claude`` once and log in. ``python3 -m vie --check`` confirms both.

This module owns the one thing that touches a process outside Python. The
rest of the analysis layer only sees ``ClaudeClient.complete`` returning a
string, so tests replace this class with a fake and never start a process.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Protocol

from src.analysis.exceptions import AnalysisAPIError

CLAUDE_PATH_ENV_VAR = "VIE_CLAUDE_PATH"
DEFAULT_TIMEOUT_SECONDS = 300
# Tools are switched off (--tools ""), so the model can only answer in text.
# A small turn allowance leaves headroom without letting a run wander.
MAX_TURNS = 3
INSTALL_HINT = (
    "Install Claude Code (https://claude.com/claude-code), run `claude` once "
    "in a terminal to log in, then try again. If it is installed somewhere "
    f"unusual, set {CLAUDE_PATH_ENV_VAR} to the full path of the executable."
)
# Removed from the child process environment. If one of these is set, Claude
# Code bills the call to that API key instead of the logged-in Claude account,
# which is exactly what the VIE is designed never to do.
_API_CREDENTIAL_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class ClaudeClient(Protocol):
    """Anything the analysis layer can send a prompt to."""

    def complete(self, system_prompt: str, user_prompt: str, model: str) -> str:
        """Send one prompt and return the model's text reply."""
        ...


class ClaudeCodeClient:
    """Sends prompts to Claude via the locally logged-in Claude Code CLI.

    Attributes:
        executable (str | None): Path to the ``claude`` executable, or None to
            resolve it from VIE_CLAUDE_PATH or the PATH at call time.
        timeout_seconds (int): Seconds to wait for one reply before giving up.
        calls (List[Dict[str, Any]]): One audit record per successful call:
            UTC start time, model requested, exact model identifiers Claude
            Code reports having used, duration, and a SHA-256 hash of the raw
            reply. The model name alone can point at different underlying
            versions over time, so these records are what make a change in
            behaviour between two runs detectable, even when its cause cannot
            be pinned down.
    """

    def __init__(
        self,
        executable: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client.

        Args:
            executable (str | None): Explicit path to ``claude``. Defaults to
                None, meaning resolve at call time.
            timeout_seconds (int): Per-call timeout in seconds.

        Raises:
            ValueError: If timeout_seconds is not positive.
        """
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.calls: List[Dict[str, Any]] = []

    def resolve_executable(self) -> str:
        """Find the ``claude`` executable.

        Returns:
            str: Path to the executable.

        Raises:
            AnalysisAPIError: If Claude Code cannot be found.
        """
        candidate = self.executable or os.environ.get(CLAUDE_PATH_ENV_VAR) or "claude"
        resolved = shutil.which(candidate)
        if resolved is None:
            raise AnalysisAPIError(f"Claude Code was not found ('{candidate}'). {INSTALL_HINT}")
        return resolved

    def complete(self, system_prompt: str, user_prompt: str, model: str) -> str:
        """Run one headless Claude Code call and return the reply text.

        The call runs in an empty temporary directory so no project files or
        CLAUDE.md from wherever the VIE was launched leak into the context,
        with the VIE system prompt replacing Claude Code's default one, and
        with every tool disabled so the model answers from the prompt alone.
        Without that, Claude Code's tools stay available and the model can
        spend its turns on them, which ends the run with error_max_turns.

        Args:
            system_prompt (str): System prompt for this call.
            user_prompt (str): The rendered user prompt.
            model (str): Model alias or identifier passed to ``--model``.

        Returns:
            str: The model's reply text.

        Raises:
            AnalysisAPIError: If Claude Code is missing, times out, exits with
                an error (for example when not logged in), or returns output
                that is not the expected result envelope.
        """
        executable = self.resolve_executable()
        started_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        clock = time.monotonic()
        command = [
            executable,
            "-p",
            user_prompt,
            "--output-format",
            "json",
            "--model",
            model,
            "--system-prompt",
            system_prompt,
            "--tools",
            "",
            "--max-turns",
            str(MAX_TURNS),
        ]
        env = {k: v for k, v in os.environ.items() if k not in _API_CREDENTIAL_ENV_VARS}

        try:
            with tempfile.TemporaryDirectory(prefix="vie-claude-") as workdir:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    cwd=workdir,
                    env=env,
                    check=False,
                )
        except FileNotFoundError as exc:
            raise AnalysisAPIError(f"Claude Code could not be started. {INSTALL_HINT}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AnalysisAPIError(
                f"Claude did not reply within {self.timeout_seconds} seconds "
                f"(model '{model}'). Try again, or check your Claude usage limits."
            ) from exc
        except OSError as exc:
            raise AnalysisAPIError(f"Claude Code could not be run: {exc}") from exc

        text = _extract_result_text(completed.stdout, completed.stderr, completed.returncode, model)
        self.calls.append(
            {
                "started_at": started_at,
                "model_requested": model,
                "models_used": _models_used(completed.stdout),
                "duration_seconds": round(time.monotonic() - clock, 1),
                "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
        return text


def _extract_result_text(stdout: str, stderr: str, returncode: int, model: str) -> str:
    """Pull the reply text out of Claude Code's JSON result envelope.

    Args:
        stdout (str): Captured standard output.
        stderr (str): Captured standard error.
        returncode (int): Process exit code.
        model (str): Model used, for error messages.

    Returns:
        str: The ``result`` text of a successful call.

    Raises:
        AnalysisAPIError: If the call failed or the envelope is malformed.
    """
    detail = (stderr.strip() or stdout.strip())[-500:]
    try:
        envelope: Any = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = None

    if not isinstance(envelope, dict):
        reason = f"exited with code {returncode}" if returncode != 0 else "returned unreadable output"
        raise AnalysisAPIError(
            f"Claude Code {reason} (model '{model}'). {_login_hint(detail)}Detail: {detail!r}"
        )

    result_text = envelope.get("result")
    if returncode != 0 or envelope.get("is_error") or envelope.get("subtype") != "success":
        raise AnalysisAPIError(
            f"Claude Code reported an error (model '{model}', "
            f"subtype '{envelope.get('subtype')}'). {_login_hint(str(result_text))}"
            f"Detail: {str(result_text or detail)[-500:]!r}"
        )

    if not isinstance(result_text, str) or not result_text.strip():
        raise AnalysisAPIError(f"Claude Code returned an empty reply (model '{model}').")
    return result_text


def _models_used(stdout: str) -> List[str]:
    """Read the exact model identifiers from Claude Code's result envelope.

    Args:
        stdout (str): Captured standard output of a successful call.

    Returns:
        List[str]: Model identifiers listed under ``modelUsage``, or an empty
        list if the envelope does not include them.
    """
    try:
        usage = json.loads(stdout).get("modelUsage")
    except (json.JSONDecodeError, AttributeError):
        return []
    return sorted(usage) if isinstance(usage, dict) else []


def _login_hint(text: str) -> str:
    """Return a login reminder if the failure text looks like an auth problem.

    Args:
        text (str): Error text from Claude Code.

    Returns:
        str: A hint sentence ending in a space, or an empty string.
    """
    lowered = text.lower()
    if any(word in lowered for word in ("login", "log in", "authenticat", "unauthori")):
        return "It looks like Claude Code is not logged in: run `claude` and log in. "
    return ""
