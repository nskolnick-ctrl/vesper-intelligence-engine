"""Fetches and validates company fundamentals for the Vesper Intelligence Engine.

Source: yfinance, chosen in the Day 2 data design because it needs no API key,
which removes an entire class of authentication and rate-limit failures from the
build phase. The fetch interface is deliberately narrow so a second source can be
added later without changing the object the analysis layer consumes.

Known limitation carried forward from the Day 2 design. A stale but plausible
figure, for example last quarter's revenue after a restatement that has not
propagated to the source, passes every check in this module, because it is the
correct type and sits inside a sane range. Nothing here cross-references a second
source, so a quietly outdated number reaches the analysis layer looking exactly
like a fresh one. The as_of_date staleness warning is a partial mitigation only:
it catches a stale snapshot, not a stale value sitting behind a current date.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import yfinance as yf

from src.data.exceptions import DataFetchError, DataValidationError
from src.data.schema import CompanyData

logger = logging.getLogger(__name__)

SOURCE_NAME = "yfinance"
MAX_SNAPSHOT_AGE_TRADING_DAYS = 5
MARGIN_FLOOR = -1.0
MARGIN_CEILING = 1.0


def fetch_company_data(ticker: str) -> CompanyData:
    """Fetch and validate one company's fundamentals.

    Args:
        ticker (str): Exchange ticker, for example "AAPL" or "DGE.L". Case is
            normalised to upper case before the call.

    Returns:
        CompanyData: A validated snapshot. Fields the source did not supply are
            set to None rather than to zero.

    Raises:
        DataFetchError: If the ticker is empty, the source call fails, or the
            response contains no usable data.
        DataValidationError: If a returned field fails its range check, for
            example a share price of zero or a margin outside -1.0 to 1.0.
    """
    if not ticker or not ticker.strip():
        raise DataFetchError("Cannot fetch company data: ticker was empty.")

    normalised = ticker.strip().upper()
    info = _fetch_raw_info(normalised)

    price = _required_float(info, ("currentPrice", "regularMarketPrice"), "price", normalised)
    as_of_date = _resolve_as_of_date(info, normalised)

    data = CompanyData(
        ticker=normalised,
        company_name=_required_str(info, ("longName", "shortName"), "company_name", normalised),
        sector=_optional_str(info, "sector"),
        price=price,
        market_cap=_optional_float(info, "marketCap"),
        fifty_two_week_low=_optional_float(info, "fiftyTwoWeekLow"),
        fifty_two_week_high=_optional_float(info, "fiftyTwoWeekHigh"),
        revenue_ttm=_optional_float(info, "totalRevenue"),
        revenue_growth_yoy=_optional_float(info, "revenueGrowth"),
        gross_margin=_optional_float(info, "grossMargins"),
        ebit_margin=_optional_float(info, "operatingMargins"),
        total_debt=_optional_float(info, "totalDebt"),
        cash_and_equivalents=_optional_float(info, "totalCash"),
        free_cash_flow_ttm=_optional_float(info, "freeCashflow"),
        pe_ratio_trailing=_optional_float(info, "trailingPE"),
        as_of_date=as_of_date,
        source=SOURCE_NAME,
    )

    _validate(data)
    _warn_if_stale(data)
    return data


def _fetch_raw_info(ticker: str) -> Dict[str, Any]:
    """Call the data source and return its raw info mapping.

    Args:
        ticker (str): Normalised ticker to fetch.

    Returns:
        Dict[str, Any]: The source's raw field mapping for the ticker.

    Raises:
        DataFetchError: If the source call raises, or returns nothing usable.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:  # noqa: BLE001 - the source raises a wide range of types
        raise DataFetchError(
            f"Fetch failed for {ticker} from {SOURCE_NAME}: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(info, dict) or not info:
        raise DataFetchError(
            f"No data returned for {ticker} from {SOURCE_NAME}. "
            "The ticker is most likely invalid or delisted."
        )
    return info


def _required_str(
    info: Dict[str, Any], keys: tuple, field_name: str, ticker: str
) -> str:
    """Return the first non-empty string among the given keys.

    Args:
        info (Dict[str, Any]): Raw source mapping.
        keys (tuple): Candidate keys, tried in order.
        field_name (str): Schema field name, used in the error message.
        ticker (str): Ticker being fetched, used in the error message.

    Returns:
        str: The resolved value.

    Raises:
        DataFetchError: If no candidate key holds a usable string.
    """
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise DataFetchError(
        f"Required field {field_name} missing for {ticker}: "
        f"none of {keys} were present in the {SOURCE_NAME} response."
    )


def _required_float(
    info: Dict[str, Any], keys: tuple, field_name: str, ticker: str
) -> float:
    """Return the first usable float among the given keys.

    Args:
        info (Dict[str, Any]): Raw source mapping.
        keys (tuple): Candidate keys, tried in order.
        field_name (str): Schema field name, used in the error message.
        ticker (str): Ticker being fetched, used in the error message.

    Returns:
        float: The resolved value.

    Raises:
        DataFetchError: If no candidate key holds a usable number.
    """
    for key in keys:
        value = _coerce_float(info.get(key))
        if value is not None:
            return value
    raise DataFetchError(
        f"Required field {field_name} missing for {ticker}: "
        f"none of {keys} were present in the {SOURCE_NAME} response."
    )


def _optional_str(info: Dict[str, Any], key: str) -> Optional[str]:
    """Return a stripped string for the key, or None where it is absent.

    Args:
        info (Dict[str, Any]): Raw source mapping.
        key (str): Key to read.

    Returns:
        Optional[str]: The value, or None if missing or blank.
    """
    value = info.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_float(info: Dict[str, Any], key: str) -> Optional[float]:
    """Return a float for the key, or None where it is absent or unusable.

    A missing field returns None and never zero, so that a field the source did
    not supply stays distinguishable from a field whose real value is zero.

    Args:
        info (Dict[str, Any]): Raw source mapping.
        key (str): Key to read.

    Returns:
        Optional[float]: The value, or None if missing, null, or not numeric.
    """
    return _coerce_float(info.get(key))


def _coerce_float(value: Any) -> Optional[float]:
    """Convert a raw source value to a float where that is meaningful.

    Args:
        value (Any): Raw value from the source.

    Returns:
        Optional[float]: The float value, or None for None, NaN, booleans, and
            anything not convertible.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN is the only value not equal to itself
        return None
    return result


def _resolve_as_of_date(info: Dict[str, Any], ticker: str) -> str:
    """Determine the ISO date the snapshot's market data refers to.

    Uses the source's own market timestamp where present, so that staleness can
    be measured against the data rather than against the moment of the call. If
    no timestamp is supplied, today's date is used and a warning is logged,
    because a snapshot dated by the caller cannot be checked for staleness.

    Args:
        info (Dict[str, Any]): Raw source mapping.
        ticker (str): Ticker being fetched, used in the log message.

    Returns:
        str: ISO formatted date, for example "2026-09-11".
    """
    timestamp = _coerce_float(info.get("regularMarketTime"))
    if timestamp is not None:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()

    logger.warning(
        "No market timestamp supplied for %s by %s. Dating the snapshot to today, "
        "which means the staleness check cannot detect an out of date source.",
        ticker,
        SOURCE_NAME,
    )
    return date.today().isoformat()


def _validate(data: CompanyData) -> None:
    """Apply every range check defined in the Day 2 schema.

    Args:
        data (CompanyData): The assembled snapshot.

    Returns:
        None

    Raises:
        DataValidationError: On the first field that fails its check.
    """
    _require_positive(data.price, "price", data.ticker)
    _require_positive(data.market_cap, "market_cap", data.ticker)
    _require_positive(data.fifty_two_week_low, "fifty_two_week_low", data.ticker)
    _require_positive(data.fifty_two_week_high, "fifty_two_week_high", data.ticker)

    low, high = data.fifty_two_week_low, data.fifty_two_week_high
    if low is not None and high is not None and low > high:
        raise DataValidationError(
            f"Invalid 52-week range for {data.ticker}: low {low} exceeds high {high}."
        )

    _require_non_negative(data.revenue_ttm, "revenue_ttm", data.ticker)
    _require_non_negative(data.total_debt, "total_debt", data.ticker)
    _require_non_negative(data.cash_and_equivalents, "cash_and_equivalents", data.ticker)

    _require_within_margin_band(data.gross_margin, "gross_margin", data.ticker)
    _require_within_margin_band(data.ebit_margin, "ebit_margin", data.ticker)


def _require_positive(value: Optional[float], field_name: str, ticker: str) -> None:
    """Raise if a present value is not greater than zero.

    Args:
        value (Optional[float]): Value to check. None passes, since a missing
            field is handled by the schema rather than by validation.
        field_name (str): Schema field name, used in the error message.
        ticker (str): Ticker being validated.

    Returns:
        None

    Raises:
        DataValidationError: If the value is present and not positive.
    """
    if value is not None and value <= 0:
        raise DataValidationError(
            f"Invalid {field_name} for {ticker}: {value}. Must be greater than zero. "
            "A zero or negative value here usually indicates a corporate action "
            "error at the source rather than a real figure."
        )


def _require_non_negative(value: Optional[float], field_name: str, ticker: str) -> None:
    """Raise if a present value is below zero.

    Args:
        value (Optional[float]): Value to check. None passes.
        field_name (str): Schema field name, used in the error message.
        ticker (str): Ticker being validated.

    Returns:
        None

    Raises:
        DataValidationError: If the value is present and negative.
    """
    if value is not None and value < 0:
        raise DataValidationError(
            f"Invalid {field_name} for {ticker}: {value}. Must be zero or greater."
        )


def _require_within_margin_band(
    value: Optional[float], field_name: str, ticker: str
) -> None:
    """Raise if a present margin sits outside the plausible band.

    Negative margins are accepted, since a loss-making company reports them
    legitimately. Only values outside -1.0 to 1.0 are rejected, because a margin
    beyond those bounds indicates the source returned a percentage, a ratio on a
    different base, or a corrupted figure.

    Args:
        value (Optional[float]): Margin as a decimal fraction. None passes.
        field_name (str): Schema field name, used in the error message.
        ticker (str): Ticker being validated.

    Returns:
        None

    Raises:
        DataValidationError: If the margin lies outside -1.0 to 1.0.
    """
    if value is None:
        return
    if not MARGIN_FLOOR <= value <= MARGIN_CEILING:
        raise DataValidationError(
            f"Invalid {field_name} for {ticker}: {value}. Must sit between "
            f"{MARGIN_FLOOR} and {MARGIN_CEILING} as a decimal fraction."
        )


def _warn_if_stale(data: CompanyData) -> None:
    """Log a warning where the snapshot is older than the permitted window.

    Args:
        data (CompanyData): The validated snapshot.

    Returns:
        None
    """
    age = _trading_days_between(date.fromisoformat(data.as_of_date), date.today())
    if age > MAX_SNAPSHOT_AGE_TRADING_DAYS:
        logger.warning(
            "Snapshot for %s is %d trading days old (as_of_date %s). Fundamentals "
            "from %s may lag.",
            data.ticker,
            age,
            data.as_of_date,
            SOURCE_NAME,
        )


def _trading_days_between(start: date, end: date) -> int:
    """Count weekdays between two dates, excluding the start date.

    Market holidays are not modelled, so this slightly overstates the number of
    trading days across a holiday week. That bias is acceptable because the
    result is only used to decide whether to warn.

    Args:
        start (date): Earlier date.
        end (date): Later date.

    Returns:
        int: Number of weekdays elapsed. Zero where end is on or before start.
    """
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor = date.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            days += 1
    return days
