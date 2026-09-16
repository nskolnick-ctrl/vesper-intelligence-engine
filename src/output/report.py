"""Turns a pipeline run into a markdown report a non-technical reader can use.

The reader should never need to know the JSON schema. Scores become "8 / 10",
enum values become plain words, and a null becomes an explicit "Not assessed"
with the reason, never a blank or a zero, carrying the Day 5 no-false-zero
rule through to the page.

This layer only formats. It makes no judgement of its own and never changes a
verdict, so everything in the report can be traced to the analysis output.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src.output.exceptions import ReportWriteError
from src.pipeline import PipelineOutput

NOT_ASSESSED = "Not assessed (the data did not support a conclusion)"
NOT_AVAILABLE = "Not available"

VERDICT_LABELS = {
    "high_quality": "High quality",
    "moderate_quality": "Moderate quality",
    "low_quality": "Low quality",
    "insufficient_data": "Insufficient data to judge",
}

VERDICT_EXPLANATIONS = {
    "high_quality": "The data points to a durable, financially sound business.",
    "moderate_quality": "Some real strengths, with weaknesses or gaps that stop it rating higher.",
    "low_quality": "The data points to weak economics or a fragile financial position.",
    "insufficient_data": "Too many key figures were missing to reach a view.",
}

MOAT_LABELS = {
    "network_effects": "Network effects",
    "switching_costs": "Switching costs",
    "intangible_assets": "Intangible assets (brand, patents, licences)",
    "cost_advantage": "Cost advantage",
    "efficient_scale": "Efficient scale",
    "none": "No clear moat",
}

FIELD_LABELS = {
    "confidence": "Confidence",
    "moat_score": "Competitive advantage (moat) score",
    "financial_quality": "Financial quality",
    "business_model_clarity": "Business model clarity",
}


def _score(value: int | None) -> str:
    """Format a 1-10 score, or the not-assessed text for None.

    Args:
        value (int | None): Score.

    Returns:
        str: For example "8 / 10".
    """
    return NOT_ASSESSED if value is None else f"{value} / 10"


def _money(value: Any) -> str:
    """Format a large amount in the company's reporting currency.

    Args:
        value (Any): Number or None.

    Returns:
        str: For example "391.0B", or "Not available".
    """
    if value is None:
        return NOT_AVAILABLE
    amount = float(value)
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(amount) >= divisor:
            return f"{amount / divisor:,.1f}{suffix}"
    return f"{amount:,.0f}"


def _percent(value: Any) -> str:
    """Format a decimal fraction as a percentage.

    Args:
        value (Any): Fraction such as 0.46, or None.

    Returns:
        str: For example "46.0%", or "Not available".
    """
    return NOT_AVAILABLE if value is None else f"{float(value) * 100:.1f}%"


def _number(value: Any, decimals: int = 2) -> str:
    """Format a plain number.

    Args:
        value (Any): Number or None.
        decimals (int): Decimal places.

    Returns:
        str: The formatted number, or "Not available".
    """
    return NOT_AVAILABLE if value is None else f"{float(value):,.{decimals}f}"


def _text(value: Any) -> str:
    """Return text, or the not-assessed text for None or blank.

    Args:
        value (Any): String or None.

    Returns:
        str: Display text.
    """
    return value.strip() if isinstance(value, str) and value.strip() else NOT_ASSESSED


def render_report(output: PipelineOutput, generated_on: date | None = None) -> str:
    """Render one pipeline run as a markdown report.

    Args:
        output (PipelineOutput): The pipeline run to report on.
        generated_on (date | None): Report date. Defaults to today.

    Returns:
        str: The complete markdown report.
    """
    generated_on = generated_on or date.today()
    data, result, bear = output.data, output.result, output.bear_case
    name = data.get("company_name") or result.ticker
    verdict_label = VERDICT_LABELS.get(result.verdict, result.verdict)
    moat_source = (
        NOT_ASSESSED
        if result.moat_primary_source is None
        else MOAT_LABELS.get(result.moat_primary_source, result.moat_primary_source)
    )

    lines: list[str] = [
        f"# {name} ({result.ticker}): Research Report",
        "",
        f"*Vesper Intelligence Engine | Report date {generated_on.isoformat()} | "
        f"Data as of {data.get('as_of_date', NOT_AVAILABLE)} (source: {data.get('source', NOT_AVAILABLE)})*",
        "",
        "## Verdict",
        "",
        f"**{verdict_label}** with confidence **{_score(result.confidence)}**",
        "",
        VERDICT_EXPLANATIONS.get(result.verdict, ""),
        "",
    ]

    if result.caveats:
        lines += ["**Read this verdict with these caveats:**", ""]
        lines += [f"- {c}" for c in result.caveats]
        lines.append("")

    if output.breaches:
        lines += [
            "> **Review flag.** This result fell below limits set *before* the analysis ran:",
            ">",
        ]
        for b in output.breaches:
            label = FIELD_LABELS.get(b.field, b.field)
            lines.append(f"> - {label} was {b.actual}, below the pre-set minimum of {b.threshold}")
        lines += [">", "> Do not rely on this verdict without a manual review.", ""]

    lines += [
        "## Scores at a glance",
        "",
        "| Measure | Score |",
        "|---|---|",
        f"| Business model clarity | {_score(result.business_model_clarity)} |",
        f"| Competitive advantage (moat) | {_score(result.moat_score)} |",
        f"| Financial quality | {_score(result.financial_quality)} |",
        f"| Confidence in this verdict | {_score(result.confidence)} |",
        "",
        "## Competitive advantage",
        "",
        f"**Main source:** {moat_source}",
        "",
        _text(result.moat_evidence),
        "",
        "## Financial quality",
        "",
        _text(result.financial_evidence),
        "",
        "## Primary risk",
        "",
        _text(result.primary_risk),
        "",
        "## The case against",
        "",
        "A separate analysis was asked to build the strongest case against this "
        "company, without seeing the verdict above.",
        "",
        f"**Bear thesis:** {_text(bear.bear_thesis)}",
        "",
        f"**Evidence:** {_text(bear.key_evidence)}",
        "",
        f"**What the positive view is assuming:** {_text(bear.bull_assumption_challenged)}",
        "",
        f"**What would prove the bear case wrong:** {_text(bear.what_would_change_this)}",
        "",
        "## Key figures used",
        "",
        "Amounts are in the company's reporting currency. \"Not available\" means the "
        "data source did not supply the figure; it has not been treated as zero.",
        "",
        "| Figure | Value |",
        "|---|---|",
        f"| Sector | {data.get('sector') or NOT_AVAILABLE} |",
        f"| Share price | {_number(data.get('price'))} |",
        f"| Market capitalisation | {_money(data.get('market_cap'))} |",
        f"| 52-week range | {_number(data.get('fifty_two_week_low'))} to {_number(data.get('fifty_two_week_high'))} |",
        f"| Revenue (last 12 months) | {_money(data.get('revenue_ttm'))} |",
        f"| Revenue growth (year on year) | {_percent(data.get('revenue_growth_yoy'))} |",
        f"| Gross margin | {_percent(data.get('gross_margin'))} |",
        f"| Operating (EBIT) margin | {_percent(data.get('ebit_margin'))} |",
        f"| Free cash flow (last 12 months) | {_money(data.get('free_cash_flow_ttm'))} |",
        f"| Total debt | {_money(data.get('total_debt'))} |",
        f"| Cash and equivalents | {_money(data.get('cash_and_equivalents'))} |",
        f"| Price to earnings (trailing) | {_number(data.get('pe_ratio_trailing'), 1)} |",
        "",
        "## How to read this report",
        "",
        "This report is generated by an AI analysis of a single snapshot of public "
        "financial data. It reasons only from the figures listed above: it does not "
        "read filings, news or management commentary, and a stale figure from the "
        "data source would not be detected. Treat it as a starting point for "
        "research, not a recommendation to buy or sell.",
        "",
        f"*Analysis model: {output.model}*",
        "",
    ]
    return "\n".join(lines)


def report_filename(ticker: str, generated_on: date) -> str:
    """Build the report file name.

    Args:
        ticker (str): Company ticker.
        generated_on (date): Report date.

    Returns:
        str: For example "AAPL_2026-09-16_report.md".
    """
    safe = "".join(ch for ch in ticker.upper() if ch.isalnum() or ch in ".-")
    return f"{safe}_{generated_on.isoformat()}_report.md"


def write_report(
    output: PipelineOutput, output_dir: Path, generated_on: date | None = None
) -> Path:
    """Render a report and write it to a dated file named after the ticker.

    Args:
        output (PipelineOutput): The pipeline run to report on.
        output_dir (Path): Directory to write into. Created if missing.
        generated_on (date | None): Report date. Defaults to today.

    Returns:
        Path: The written file.

    Raises:
        ReportWriteError: If the directory or file cannot be written.
    """
    generated_on = generated_on or date.today()
    path = Path(output_dir) / report_filename(output.result.ticker, generated_on)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_report(output, generated_on), encoding="utf-8")
    except OSError as exc:
        raise ReportWriteError(f"Could not write the report to {path}: {exc}") from exc
    return path
