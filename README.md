# Vesper Intelligence Engine | Noah Skolnick

A Python command line research tool. Given a ticker, it fetches company
fundamentals, runs them through a two-call Claude analysis (a verdict and an
independent bear case), and writes a plain-language markdown research report.

**No API key is needed.** Claude runs through the Claude Code login of whoever
runs the engine, so it uses your own Claude account.

Built over the Vesper Edge Internship. The design phase (Days 1 to 4) fixed the
architecture, the data schema, the prompt library and the evaluation framework
before any code was written, so this repository implements a specification rather
than discovering one as it goes.

## Quick start (under 10 minutes)

You need Python 3.10+ and a Claude account.

**1. Install Claude Code and log in (one time).** Follow
<https://claude.com/claude-code>, then run `claude` once in a terminal and log
in with your Claude account. Type `/exit` to leave.

**2. Install the engine.**

```bash
git clone https://github.com/nskolnick-ctrl/vesper-intelligence-engine.git
cd vesper-intelligence-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Confirm Claude is reachable.**

```bash
python3 -m vie --check
```

**4. Run an analysis.**

```bash
python3 -m vie AAPL
```

The report is written to `reports/AAPL_<date>_report.md`. Add `--pdf` for a
PDF copy. A run takes a minute
or two, because it makes two Claude calls.

## Command line options

| Option | What it does |
|---|---|
| `TICKER` | Company to research, e.g. `AAPL`, `BRK-B`, `DGE.L` |
| `--output-dir DIR` | Where to write the report (default `reports/`) |
| `--pdf` | Also write a PDF copy of the report |
| `--json` | Also write the validated JSON next to the report |
| `--min FIELD=VALUE` | Pre-commit a minimum score before the run, e.g. `--min confidence=7`. Repeatable. The report carries a review flag if the result falls below it |
| `--model NAME` | Claude model alias (default `sonnet`, or set `VIE_MODEL`) |
| `--check` | Check Claude Code is installed and logged in |
| `--debug` | Show full tracebacks |

Any error prints one readable line and exits with code 1. No traceback is shown
unless you pass `--debug`.

## Layers

| Layer | Package | Job |
|---|---|---|
| Data | `src/data/` | Fetch and validate the 17-field company snapshot (yfinance) |
| Analysis | `src/analysis/` | Two Claude calls via Claude Code, JSON parsing, schema validation, bear case override |
| Pipeline | `src/pipeline.py` | Wires data to analysis; `run_analysis("AAPL")` returns JSON |
| Output | `src/output/` | Turns a run into a markdown report for a non-technical reader |
| CLI | `vie/` | `python3 -m vie TICKER`; the only layer that catches every exception |

## How Claude is called

`src/analysis/claude_runner.py` runs Claude Code in headless mode
(`claude -p ... --output-format json`) in an empty temporary directory, with the
VIE system prompt and a single turn. It removes `ANTHROPIC_API_KEY` from the
environment it passes on, so a stray key in your shell can never be billed.
If Claude Code is somewhere unusual, set `VIE_CLAUDE_PATH`.

Claude Code does not expose a temperature setting, so output can vary slightly
between runs. Every reply is parsed strictly and validated against the schema
before anything reaches the report.

## Running the tests

```bash
pytest tests/
```

The tests never start Claude Code or touch the network: the data source and
the Claude client are faked at their boundaries.

## Generating test-set outputs

```bash
python3 scripts/generate_outputs.py            # AAPL MSFT KO JPM TSLA
python3 scripts/generate_outputs.py AAPL NVDA  # your own list
```

Writes validated JSON to `tests/outputs/`, which `tests/test_saved_outputs.py`
re-checks against the schemas.

## Using the layers from Python

```python
from src.pipeline import run_analysis
print(run_analysis("AAPL"))            # full run, JSON string

from src.data import fetch_company_data
data = fetch_company_data("AAPL")      # data layer only
print(data.gross_margin, data.as_of_date)
```

`fetch_company_data` raises `DataFetchError` when the source call fails or the
ticker is unusable, and `DataValidationError` when a returned field cannot be
true, such as a negative share price. Any field the source does not supply is
set to `None`, never to zero, and the report shows it as "Not available".

## Environment variables

None are required. `.env.example` lists the optional ones (`VIE_MODEL`,
`VIE_CLAUDE_PATH`).

## Known limitation of the data layer

A stale but plausible figure passes every check in this module. If the source
serves last quarter's revenue after a restatement that has not yet propagated,
the value is the right type and inside a sane range, so nothing rejects it.
Nothing here cross-references a second source, so an outdated number reaches the
analysis layer looking exactly like a fresh one.

The `as_of_date` staleness warning is a partial mitigation. It uses the source's
own market timestamp where one is supplied, so it catches a stale snapshot, but
it cannot catch a stale value sitting behind a current date.

This is a deliberate choice rather than an oversight. Adding a second source to
cross-reference would double the authentication and rate-limit failure surface
during the build phase, which is the exact class of failure the single-source
design was chosen to avoid. The fetch interface is narrow enough that a second
source can be added later without changing the object the analysis layer
consumes.
