"""Generate validated JSON outputs for the evaluation test set.

Runs the full pipeline (data layer, then two Claude calls through your own
Claude Code login) for each ticker and writes tests/outputs/<TICKER>.json.
Every file is re-parsed after writing, so a file only exists if it is valid.

Usage (from the repository root):
    python3 scripts/generate_outputs.py                # default five tickers
    python3 scripts/generate_outputs.py AAPL MSFT KO   # your own set
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # macOS LibreSSL notice
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import run_analysis  # noqa: E402

DEFAULT_TICKERS = ["AAPL", "MSFT", "KO", "JPM", "TSLA"]
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "outputs"


def main(tickers: list[str]) -> int:
    """Generate one JSON output per ticker.

    Args:
        tickers (list[str]): Tickers to run.

    Returns:
        int: 0 if every ticker succeeded, 1 otherwise.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for ticker in tickers:
        print(f"{ticker}: running...", flush=True)
        try:
            output = run_analysis(ticker)
            json.loads(output)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"{ticker}: FAILED ({type(exc).__name__}: {exc})")
            continue
        path = OUTPUT_DIR / f"{ticker.upper()}.json"
        path.write_text(output + "\n", encoding="utf-8")
        print(f"{ticker}: wrote {path.relative_to(OUTPUT_DIR.parent.parent)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULT_TICKERS))
