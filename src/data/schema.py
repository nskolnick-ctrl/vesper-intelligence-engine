"""The clean company data object produced by the VIE data layer.

These 17 fields are the canonical VIE Data Layer Schema fixed in the Day 2
design. A field that cannot be sourced is set to None, never to a default of
zero, because a false zero is silently indistinguishable from a genuine zero
once the object reaches the analysis layer.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CompanyData:
    """A validated snapshot of one company's fundamentals.

    Attributes:
        ticker (str): Ticker the data was fetched for, upper-cased.
        company_name (str): Company name as reported by the source.
        sector (Optional[str]): Sector classification. None where the source
            does not classify the ticker.
        price (float): Latest share price. Always greater than zero.
        market_cap (Optional[float]): Market capitalisation in the reporting
            currency. Greater than zero where present.
        fifty_two_week_low (Optional[float]): 52-week low. Greater than zero and
            no greater than fifty_two_week_high where both are present.
        fifty_two_week_high (Optional[float]): 52-week high. Greater than zero
            and no less than fifty_two_week_low where both are present.
        revenue_ttm (Optional[float]): Trailing twelve month revenue. Zero or
            greater, since zero is valid for a pre-revenue company.
        revenue_growth_yoy (Optional[float]): Year on year revenue growth as a
            decimal fraction. Not range-bound, since growth can be sharply
            negative or triple-digit positive off a small base.
        gross_margin (Optional[float]): Gross margin as a decimal fraction,
            constrained to -1.0 to 1.0. Negative values are valid for
            loss-making companies.
        ebit_margin (Optional[float]): EBIT margin as a decimal fraction, same
            -1.0 to 1.0 band as gross_margin.
        total_debt (Optional[float]): Total debt. Zero or greater.
        cash_and_equivalents (Optional[float]): Cash and equivalents. Zero or
            greater.
        free_cash_flow_ttm (Optional[float]): Trailing twelve month free cash
            flow. Not range-bound, since negative FCF is a real value.
        pe_ratio_trailing (Optional[float]): Trailing price to earnings ratio.
            Not range-checked, because a negative or extreme P/E arising from
            negative earnings is a real value rather than an error.
        as_of_date (str): ISO date the snapshot was taken.
        source (str): Name of the data source, recorded for provenance.
    """

    ticker: str
    company_name: str
    sector: Optional[str]
    price: float
    market_cap: Optional[float]
    fifty_two_week_low: Optional[float]
    fifty_two_week_high: Optional[float]
    revenue_ttm: Optional[float]
    revenue_growth_yoy: Optional[float]
    gross_margin: Optional[float]
    ebit_margin: Optional[float]
    total_debt: Optional[float]
    cash_and_equivalents: Optional[float]
    free_cash_flow_ttm: Optional[float]
    pe_ratio_trailing: Optional[float]
    as_of_date: str
    source: str
