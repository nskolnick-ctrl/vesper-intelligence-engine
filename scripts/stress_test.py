"""Day 9 stress test: run the VIE on ten companies chosen to break it.

Each company is picked for a specific way the VIE could fail (the Day 9
brief's categories). For every run the script records whether it succeeded,
what came back, which inputs were missing, how long it took and which exact
Claude models answered. Reports and JSON for each run are saved, and a results
table is written with blank Day 4 rubric columns to score by hand.

Usage (from the repository root, takes roughly 15-25 minutes):
    python3 scripts/stress_test.py
    python3 scripts/stress_test.py --only BARC.L DGE.L   # rerun a subset

Outputs go to reports/stress_test/ (git-ignored with the rest of reports/).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # macOS LibreSSL notice
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.output import write_report  # noqa: E402
from src.pipeline import PipelineOutput, run_pipeline_full  # noqa: E402

OUTPUT_DIR = ROOT / "reports" / "stress_test"
RUBRIC_COLUMNS = [
    "Moat evidence (1-5)",
    "Financial accuracy (1-5)",
    "Null handling (1-5)",
    "Peer validity (1-5)",
    "Verdict alignment (1-5)",
]
GATE_MIN_SUCCESSES = 8


@dataclass
class StressCase:
    """One company in the stress test.

    Attributes:
        ticker (str): Yahoo Finance ticker.
        category (str): Day 9 brief category the company represents.
        why (str): The specific failure this company is meant to provoke.
    """

    ticker: str
    category: str
    why: str


DEFAULT_CASES: List[StressCase] = [
    StressCase("MSFT", "Large US tech", "Baseline; last run questioned its own free cash flow figure"),
    StressCase("NVDA", "Large US tech", "Extreme growth and valuation; tests whether verdict tracks data"),
    StressCase("JDW.L", "Mid-size UK", "Pence pricing, GBP accounts, thin-margin pub operator"),
    StressCase("WOSG.L", "Mid-size UK", "Pence pricing, luxury retail with debt"),
    StressCase("CVV", "Limited public data", "Micro-cap with sparse coverage; tests null handling"),
    StressCase("FIG", "Recent IPO", "Short history; tests growth figures and missing P/E"),
    StressCase("BYND", "Negative earnings", "Losses and cash burn; negative margins and no P/E"),
    StressCase("SWBI", "Niche sector", "Firearms maker; unusual sector for generic moat language"),
    StressCase("BARC.L", "Own choice: bank", "Known failure: industrial framework applied to a bank"),
    StressCase("DGE.L", "Own choice: brand", "Day 4 test: brand pricing power versus marketing spend"),
]


@dataclass
class StressResult:
    """Outcome of one stress test run.

    Attributes:
        case (StressCase): The company that was run.
        succeeded (bool): Whether a validated report was produced.
        seconds (float): Wall-clock duration of the run.
        output (Optional[PipelineOutput]): The run output when it succeeded.
        error (str): Error type and message when it failed, else empty.
        report_path (str): Path of the written report, else empty.
        notes (Dict[str, Any]): Extra observations, such as missing inputs.
    """

    case: StressCase
    succeeded: bool
    seconds: float
    output: Optional[PipelineOutput] = None
    error: str = ""
    report_path: str = ""
    notes: Dict[str, Any] = field(default_factory=dict)


def run_case(
    case: StressCase,
    runner: Callable[..., PipelineOutput] = run_pipeline_full,
    output_dir: Path = OUTPUT_DIR,
) -> StressResult:
    """Run the full pipeline for one company without letting it crash the test.

    Args:
        case (StressCase): Company to run.
        runner (Callable[..., PipelineOutput]): Pipeline function, replaceable
            in tests.
        output_dir (Path): Where reports and JSON are written.

    Returns:
        StressResult: Success or failure, with details either way.
    """
    started = time.monotonic()
    try:
        output = runner(case.ticker)
        report = write_report(output, output_dir)
        report.with_suffix(".json").write_text(output.to_json() + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - a failure is a result, not a crash
        return StressResult(
            case=case,
            succeeded=False,
            seconds=round(time.monotonic() - started, 1),
            error=f"{type(exc).__name__}: {exc}"[:400],
        )

    missing = sorted(k for k, v in output.data.items() if v is None)
    calls = output.run_metadata.get("claude_calls", []) if output.run_metadata else []
    return StressResult(
        case=case,
        succeeded=True,
        seconds=round(time.monotonic() - started, 1),
        output=output,
        report_path=str(report.relative_to(ROOT)) if report.is_relative_to(ROOT) else str(report),
        notes={
            "missing_inputs": missing,
            "models_used": sorted({m for c in calls for m in c.get("models_used", [])}),
            "downgraded_by_bear_case": any(
                "contests the evidence" in c for c in output.result.caveats
            ),
        },
    )


def results_rows(results: List[StressResult]) -> List[Dict[str, Any]]:
    """Flatten results into table rows, with blank rubric columns.

    Args:
        results (List[StressResult]): Stress test outcomes.

    Returns:
        List[Dict[str, Any]]: One row per company.
    """
    rows = []
    for r in results:
        res = r.output.result if r.output else None
        row: Dict[str, Any] = {
            "Ticker": r.case.ticker,
            "Category": r.case.category,
            "Succeeded": "Yes" if r.succeeded else "No",
            "Verdict": res.verdict.replace("_", " ") if res else "",
            "Confidence": res.confidence if res else "",
            "Moat score": (res.moat_score if res.moat_score is not None else "null") if res else "",
            "Missing inputs": ", ".join(r.notes.get("missing_inputs", [])) or ("none" if r.succeeded else ""),
            "Bear downgrade": ("Yes" if r.notes.get("downgraded_by_bear_case") else "No") if r.succeeded else "",
            "Seconds": r.seconds,
            "Error": r.error,
            "Why chosen": r.case.why,
        }
        row.update({col: "" for col in RUBRIC_COLUMNS})
        rows.append(row)
    return rows


def write_results(results: List[StressResult], output_dir: Path = OUTPUT_DIR) -> Path:
    """Write results as CSV and a markdown table, and return the markdown path.

    Args:
        results (List[StressResult]): Stress test outcomes.
        output_dir (Path): Destination directory.

    Returns:
        Path: The markdown results file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = results_rows(results)
    stamp = date.today().isoformat()

    csv_path = output_dir / f"stress_test_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    successes = sum(r.succeeded for r in results)
    gate = "MET" if successes >= GATE_MIN_SUCCESSES else "NOT MET"
    headers = ["Ticker", "Category", "Succeeded", "Verdict", "Confidence", "Moat score",
               "Missing inputs", "Bear downgrade", "Seconds", "Error"] + RUBRIC_COLUMNS
    lines = [
        f"# VIE Stress Test Results ({stamp})",
        "",
        f"**{successes} of {len(results)} companies succeeded.** Quality gate 11 "
        f"(at least {GATE_MIN_SUCCESSES} of 10): **{gate}**.",
        "",
        "Rubric columns are blank on purpose: score each report by hand against the Day 4 rubric.",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for row in rows:
        cells = [str(row[h]).replace("|", "/").replace("\n", " ") for h in headers]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Why each company was chosen", ""]
    lines += [f"- **{r.case.ticker}** ({r.case.category}): {r.case.why}" for r in results]
    md_path = output_dir / f"stress_test_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def main(argv: Optional[List[str]] = None) -> int:
    """Run the stress test from the command line.

    Args:
        argv (Optional[List[str]]): Arguments, excluding the program name.

    Returns:
        int: 0 if quality gate 11 is met, otherwise 1.
    """
    parser = argparse.ArgumentParser(description="Run the VIE Day 9 stress test.")
    parser.add_argument("--only", nargs="+", metavar="TICKER", help="Run only these tickers from the list")
    args = parser.parse_args(argv)

    cases = DEFAULT_CASES
    if args.only:
        wanted = {t.upper() for t in args.only}
        cases = [c for c in DEFAULT_CASES if c.ticker in wanted]
        if not cases:
            print(f"None of {sorted(wanted)} are in the stress test list.")
            return 1

    results = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.ticker} ({case.category})...", flush=True)
        result = run_case(case)
        status = "ok" if result.succeeded else f"FAILED - {result.error}"
        print(f"    {status} in {result.seconds}s", flush=True)
        results.append(result)

    path = write_results(results)
    successes = sum(r.succeeded for r in results)
    print(f"\n{successes}/{len(results)} succeeded. Results table: {path.relative_to(ROOT)}")
    return 0 if successes >= min(GATE_MIN_SUCCESSES, len(results)) else 1


if __name__ == "__main__":
    sys.exit(main())
