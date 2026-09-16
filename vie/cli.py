"""Command-line interface for the Vesper Intelligence Engine.

Takes a ticker, runs the full pipeline (data, analysis, output) and writes a
dated markdown report. Every failure is turned into one readable line on
stderr and exit code 1. ``--debug`` shows the full traceback for developers.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import Callable, Sequence

from src.analysis import (
    AnalysisAPIError,
    ClaudeCodeClient,
    ResponseParsingError,
    SchemaValidationError,
)
from src.analysis.engine import DEFAULT_MODEL
from src.analysis.schema import ANALYSIS_FIELDS
from src.data import DataFetchError, DataValidationError
from src.output import ReportWriteError, write_report
from src.pipeline import PipelineConversionError, PipelineOutput, run_pipeline_full

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130

TICKER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-=^]{0,14}$")
THRESHOLD_FIELDS = sorted(
    name for name, (types, _, _) in ANALYSIS_FIELDS.items() if types == (int,)
)

PipelineRunner = Callable[..., PipelineOutput]


class CliInputError(Exception):
    """Raised when a command-line argument is unusable before any work starts."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m vie",
        description=(
            "Vesper Intelligence Engine: research one company and write a markdown "
            "report. Claude runs through your own Claude Code login; no API key is needed."
        ),
    )
    parser.add_argument("ticker", nargs="?", help="Company ticker, for example AAPL or DGE.L")
    parser.add_argument(
        "--output-dir",
        default="reports",
        type=Path,
        help="Directory for the report (default: reports/)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model alias or name (default: {DEFAULT_MODEL}, or VIE_MODEL)",
    )
    parser.add_argument(
        "--min",
        dest="thresholds",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=(
            "Pre-commit a minimum score before the analysis runs, for example "
            f"--min confidence=7. Repeatable. Fields: {', '.join(THRESHOLD_FIELDS)}"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also write the validated JSON output next to the report",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that Claude Code is installed and logged in, then exit",
    )
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks on error")
    return parser


def validate_ticker(ticker: str | None) -> str:
    """Check a ticker is plausibly shaped before any network call.

    Args:
        ticker (str | None): Raw ticker argument.

    Returns:
        str: The upper-cased ticker.

    Raises:
        CliInputError: If the ticker is missing or cannot be a real ticker.
    """
    if not ticker or not ticker.strip():
        raise CliInputError("No ticker given. Usage: python3 -m vie AAPL")
    cleaned = ticker.strip()
    if not TICKER_PATTERN.match(cleaned):
        raise CliInputError(
            f"'{cleaned}' is not a valid ticker. Tickers are up to 15 letters, digits, "
            "dots or dashes, for example AAPL, BRK-B or DGE.L."
        )
    return cleaned.upper()


def parse_thresholds(raw: Sequence[str]) -> dict[str, int] | None:
    """Parse repeated ``--min FIELD=VALUE`` arguments.

    Args:
        raw (Sequence[str]): Values as given on the command line.

    Returns:
        dict[str, int] | None: Field to minimum value, or None if none given.

    Raises:
        CliInputError: If an entry is malformed or names an unknown field.
    """
    if not raw:
        return None
    thresholds: dict[str, int] = {}
    for item in raw:
        name, sep, value = item.partition("=")
        name = name.strip()
        if not sep or name not in THRESHOLD_FIELDS:
            raise CliInputError(
                f"Invalid --min '{item}'. Use FIELD=VALUE with FIELD one of: "
                f"{', '.join(THRESHOLD_FIELDS)}."
            )
        try:
            number = int(value)
        except ValueError:
            raise CliInputError(f"Invalid --min '{item}': value must be a whole number 1-10.") from None
        if not 1 <= number <= 10:
            raise CliInputError(f"Invalid --min '{item}': value must be between 1 and 10.")
        thresholds[name] = number
    return thresholds


def _describe_error(exc: BaseException, ticker: str) -> str:
    """Turn any pipeline exception into one readable sentence.

    Args:
        exc (BaseException): The exception raised.
        ticker (str): Ticker being processed.

    Returns:
        str: The message to show the user.
    """
    if isinstance(exc, CliInputError):
        return str(exc)
    if isinstance(exc, DataFetchError):
        return f"Could not get data for {ticker}. Check the ticker is correct and you are online. ({exc})"
    if isinstance(exc, DataValidationError):
        return f"The data source returned figures for {ticker} that failed validation. ({exc})"
    if isinstance(exc, PipelineConversionError):
        return f"Internal error passing {ticker}'s data to the analysis. ({exc})"
    if isinstance(exc, AnalysisAPIError):
        return f"Claude could not complete the analysis. {exc}"
    if isinstance(exc, (ResponseParsingError, SchemaValidationError)):
        return (
            f"Claude's answer for {ticker} did not pass validation, so no report was "
            f"written. Running again usually fixes this. ({exc})"
        )
    if isinstance(exc, ReportWriteError):
        return str(exc)
    return f"Unexpected error ({type(exc).__name__}): {exc}. Run again with --debug for details."


def _configure_logging(debug: bool) -> None:
    """Send warnings to stderr and silence noisy third-party loggers.

    Args:
        debug (bool): Whether to show debug-level logging.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    if not debug:
        for noisy in ("yfinance", "urllib3", "peewee", "curl_cffi"):
            logging.getLogger(noisy).setLevel(logging.CRITICAL)


def run_check(model: str, client: ClaudeCodeClient | None = None) -> int:
    """Confirm Claude Code is installed, logged in and answering.

    Args:
        model (str): Model to test.
        client (ClaudeCodeClient | None): Client to use; defaults to a new one.

    Returns:
        int: Exit code.
    """
    active = client or ClaudeCodeClient(timeout_seconds=120)
    print(f"Checking Claude Code (model: {model})...", file=sys.stderr)
    active.complete(
        system_prompt="You are a connectivity check.",
        user_prompt="Reply with the single word OK.",
        model=model,
    )
    print("Claude Code is installed, logged in and responding. The VIE is ready.")
    return EXIT_OK


def main(argv: Sequence[str] | None = None, runner: PipelineRunner = run_pipeline_full) -> int:
    """Run the CLI.

    Args:
        argv (Sequence[str] | None): Arguments, excluding the program name.
            Defaults to sys.argv[1:].
        runner (PipelineRunner): Pipeline function, replaceable in tests.

    Returns:
        int: 0 on success, 1 on any error, 130 if interrupted.
    """
    args = build_parser().parse_args(argv)
    _configure_logging(args.debug)
    ticker = (args.ticker or "").strip().upper()

    try:
        if args.check:
            try:
                return run_check(args.model)
            except AnalysisAPIError as exc:
                print(f"Error: Claude Code check failed. {exc}", file=sys.stderr)
                return EXIT_ERROR

        ticker = validate_ticker(args.ticker)
        thresholds = parse_thresholds(args.thresholds)

        print(f"Researching {ticker}: fetching data, then two Claude calls. This takes a minute or two.", file=sys.stderr)
        output = runner(ticker, override_thresholds=thresholds, model=args.model)
        report_path = write_report(output, args.output_dir)

        if args.json:
            json_path = report_path.with_suffix(".json")
            try:
                json_path.write_text(output.to_json() + "\n", encoding="utf-8")
            except OSError as exc:
                raise ReportWriteError(f"Could not write the JSON output to {json_path}: {exc}") from exc
            print(f"JSON written to {json_path}")

    except KeyboardInterrupt:
        print("\nInterrupted. No report written.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - the CLI is the one layer that catches everything
        if args.debug:
            traceback.print_exc()
        print(f"Error: {_describe_error(exc, ticker or '(no ticker)')}", file=sys.stderr)
        return EXIT_ERROR

    result = output.result
    print(f"Report written to {report_path}")
    print(f"Verdict: {result.verdict.replace('_', ' ')} (confidence {result.confidence}/10)")
    if output.breaches:
        print("Review flag: result fell below a pre-committed minimum. See the report.")
    return EXIT_OK
