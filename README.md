# Vesper Intelligence Engine — Noah Skolnick

A Python command line research tool. Given a ticker, it fetches company
fundamentals, runs them through a Claude-powered analysis pipeline, and produces
a structured investment research report.

Built over the Vesper Edge Internship. The design phase (Days 1 to 4) fixed the
architecture, the data schema, the prompt library and the evaluation framework
before any code was written, so this repository implements a specification rather
than discovering one as it goes.

## Layers

| Layer | Package | Status |
|---|---|---|
| Data | `src/data/` | Built |
| Analysis | `src/analysis/` | Day 6 |
| Output | `src/output/` | Day 8 |

## Setup

Use a virtual environment, and install and run tests with the same interpreter.
Installing with one Python and running `pytest` from another is the most common
way this suite appears broken when it is not.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the tests

```bash
pytest tests/
```

## Using the data layer

```python
from src.data import fetch_company_data

data = fetch_company_data("AAPL")
print(data.gross_margin, data.as_of_date)
```

`fetch_company_data` returns a `CompanyData` object holding the 17 fields fixed
in the Day 2 schema. It raises `DataFetchError` when the source call fails or the
ticker is unusable, and `DataValidationError` when a returned field cannot be
true, such as a negative share price.

Any field the source does not supply is set to `None`, never to zero. A false
zero would be silently indistinguishable from a real one further down the
pipeline.

## Environment variables

`.env.example` lists the variables the engine expects. Only
`ANTHROPIC_API_KEY` is needed, and not until the Day 6 analysis layer, because
the data source requires no API key. Copy it to `.env`, which is git-ignored.

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
