"""Tests for the VIE data layer.

Every test imports only the public interface exported by ``src.data``. Nothing
here reaches into the fetch implementation, so the internals can be refactored
without rewriting these tests. The one exception is the patch target, which
replaces the third party source rather than any function of ours.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from src.data import (
    CompanyData,
    DataFetchError,
    DataValidationError,
    fetch_company_data,
)


def _epoch_days_ago(days: int) -> float:
    """Return a UTC epoch timestamp the given number of days in the past.

    Args:
        days (int): How many days back to place the timestamp.

    Returns:
        float: Epoch seconds.
    """
    return (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp()


def _complete_info() -> dict:
    """Return a source payload with every schema field populated.

    Returns:
        dict: Raw source mapping representing a healthy, profitable company.
    """
    return {
        "longName": "Example Industries plc",
        "sector": "Industrials",
        "currentPrice": 128.40,
        "marketCap": 9_600_000_000,
        "fiftyTwoWeekLow": 96.10,
        "fiftyTwoWeekHigh": 141.75,
        "totalRevenue": 3_200_000_000,
        "revenueGrowth": 0.084,
        "grossMargins": 0.412,
        "operatingMargins": 0.176,
        "totalDebt": 1_100_000_000,
        "totalCash": 480_000_000,
        "freeCashflow": 265_000_000,
        "trailingPE": 18.6,
        "regularMarketTime": _epoch_days_ago(1),
    }


class _FakeTicker:
    """Stand-in for the source's Ticker object.

    Attributes:
        info (dict): Payload returned to the caller.
    """

    def __init__(self, payload: dict) -> None:
        """Store the payload this fake will return.

        Args:
            payload (dict): Mapping to serve as the ``info`` attribute.
        """
        self.info = payload


@pytest.fixture
def source(monkeypatch):
    """Replace the third party source with a controllable fake.

    Args:
        monkeypatch: pytest's attribute patching fixture.

    Returns:
        Callable[[object], None]: Call it with a payload dict to have the source
            return that payload, or with an exception instance to have the
            source raise it.
    """
    import src.data.fetcher as fetcher

    def _serve(payload_or_error):
        def _factory(ticker):
            if isinstance(payload_or_error, BaseException):
                raise payload_or_error
            return _FakeTicker(payload_or_error)

        monkeypatch.setattr(fetcher.yf, "Ticker", _factory)

    return _serve


def test_happy_path_returns_fully_populated_object(source):
    """A complete source payload produces a populated, correctly typed object."""
    source(_complete_info())

    data = fetch_company_data("exi.l")

    assert isinstance(data, CompanyData)
    assert data.ticker == "EXI.L"
    assert data.company_name == "Example Industries plc"
    assert data.sector == "Industrials"
    assert data.price == pytest.approx(128.40)
    assert data.gross_margin == pytest.approx(0.412)
    assert data.free_cash_flow_ttm == pytest.approx(265_000_000)
    assert data.source == "yfinance"


def test_missing_fields_become_none_never_zero(source):
    """Fields the source omits are None, so a gap stays distinct from a real zero."""
    payload = _complete_info()
    for absent in ("marketCap", "totalDebt", "freeCashflow", "sector", "trailingPE"):
        del payload[absent]
    payload["grossMargins"] = float("nan")
    source(payload)

    data = fetch_company_data("EXI.L")

    assert data.market_cap is None
    assert data.total_debt is None
    assert data.free_cash_flow_ttm is None
    assert data.sector is None
    assert data.pe_ratio_trailing is None
    assert data.gross_margin is None


def test_real_zero_is_preserved(source):
    """A genuine zero survives, confirming the null handling is not blanket coercion."""
    payload = _complete_info()
    payload["totalDebt"] = 0
    payload["totalRevenue"] = 0
    source(payload)

    data = fetch_company_data("EXI.L")

    assert data.total_debt == 0
    assert data.revenue_ttm == 0


def test_source_failure_raises_datafetcherror(source):
    """An exception from the source surfaces as DataFetchError naming the ticker."""
    source(ConnectionError("connection reset by peer"))

    with pytest.raises(DataFetchError) as excinfo:
        fetch_company_data("EXI.L")

    message = str(excinfo.value)
    assert "EXI.L" in message
    assert "ConnectionError" in message


def test_empty_response_raises_datafetcherror(source):
    """An empty payload, which is how the source reports an unknown ticker, fails loudly."""
    source({})

    with pytest.raises(DataFetchError):
        fetch_company_data("NOT_A_TICKER")


def test_blank_ticker_raises_datafetcherror():
    """A blank ticker fails before any network call is attempted."""
    with pytest.raises(DataFetchError):
        fetch_company_data("   ")


def test_negative_price_raises_datavalidationerror(source):
    """A non-positive price is rejected rather than propagated into valuation work."""
    payload = _complete_info()
    payload["currentPrice"] = -4.2
    source(payload)

    with pytest.raises(DataValidationError) as excinfo:
        fetch_company_data("EXI.L")

    assert "price" in str(excinfo.value)


def test_inverted_52_week_range_raises_datavalidationerror(source):
    """A low above the high cannot be true and is rejected."""
    payload = _complete_info()
    payload["fiftyTwoWeekLow"] = 200.0
    payload["fiftyTwoWeekHigh"] = 150.0
    source(payload)

    with pytest.raises(DataValidationError):
        fetch_company_data("EXI.L")


def test_negative_margins_are_accepted(source):
    """Loss-making companies report negative margins, which are valid, not errors."""
    payload = _complete_info()
    payload["grossMargins"] = -0.31
    payload["operatingMargins"] = -0.88
    payload["freeCashflow"] = -142_000_000
    source(payload)

    data = fetch_company_data("EXI.L")

    assert data.gross_margin == pytest.approx(-0.31)
    assert data.ebit_margin == pytest.approx(-0.88)
    assert data.free_cash_flow_ttm == pytest.approx(-142_000_000)


def test_margin_outside_plausible_band_raises_datavalidationerror(source):
    """A margin above 1.0 means the source sent a percentage, so it is rejected."""
    payload = _complete_info()
    payload["grossMargins"] = 41.2
    source(payload)

    with pytest.raises(DataValidationError):
        fetch_company_data("EXI.L")


def test_stale_snapshot_warns_but_still_returns(source, caplog):
    """An old market timestamp produces a warning rather than a silent pass."""
    payload = _complete_info()
    payload["regularMarketTime"] = _epoch_days_ago(21)
    source(payload)

    with caplog.at_level(logging.WARNING):
        data = fetch_company_data("EXI.L")

    assert isinstance(data, CompanyData)
    assert any("trading days old" in record.message for record in caplog.records)


def test_zero_gross_margin_with_positive_operating_margin_becomes_none(source):
    """Day 9: a bank-style 0.0 gross margin that contradicts EBIT is treated as missing."""
    payload = _complete_info()
    payload["sector"] = "Financial Services"
    payload["grossMargins"] = 0.0
    payload["operatingMargins"] = 0.43
    source(payload)

    data = fetch_company_data("BARC.L")

    assert data.gross_margin is None
    assert data.ebit_margin == pytest.approx(0.43)


def test_zero_gross_margin_kept_when_operating_margin_not_positive(source):
    """A zero gross margin alongside a loss is possible, so it is preserved."""
    payload = _complete_info()
    payload["grossMargins"] = 0.0
    payload["operatingMargins"] = -0.2
    source(payload)

    assert fetch_company_data("EXI.L").gross_margin == 0


def test_currency_fields_are_captured(source):
    """Day 9: price and statement currencies are recorded so pence can be labelled."""
    payload = _complete_info()
    payload["currency"] = "GBp"
    payload["financialCurrency"] = "GBP"
    source(payload)

    data = fetch_company_data("EXI.L")

    assert data.currency == "GBp"
    assert data.financial_currency == "GBP"
