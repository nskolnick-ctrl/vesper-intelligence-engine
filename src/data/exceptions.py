"""Exceptions raised by the VIE data layer.

Both exceptions are raised before the data object leaves the data layer, so
nothing invalid is ever passed downstream silently.
"""


class DataLayerError(Exception):
    """Base class for every error raised by the data layer."""


class DataFetchError(DataLayerError):
    """Raised when the data source call itself fails.

    Covers an invalid ticker, a network failure, or a response containing no
    usable data. The message always names the ticker and the underlying cause so
    the failure can be diagnosed without re-running the call.
    """


class DataValidationError(DataLayerError):
    """Raised when a fetched field fails its range or type check.

    Covers values that the source returned successfully but that cannot be true,
    such as a negative share price or a margin outside the plausible band.
    """
