"""Public interface of the VIE data layer.

Everything the rest of the engine is allowed to import from this package is
listed here. The analysis and output layers depend on these names only, never on
the modules behind them, so the fetch implementation can change without
rippling outward.
"""

from src.data.exceptions import DataFetchError, DataLayerError, DataValidationError
from src.data.fetcher import fetch_company_data
from src.data.schema import CompanyData

__all__ = [
    "CompanyData",
    "DataFetchError",
    "DataLayerError",
    "DataValidationError",
    "fetch_company_data",
]
