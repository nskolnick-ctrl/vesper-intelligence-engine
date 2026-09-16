"""VIE Layer 4: Command-line interface.

Run from the repository root:

    python3 -m vie AAPL

This is the only layer allowed to catch every exception (Day 1 architecture),
so the user sees an informative message and a non-zero exit code, never a
Python traceback.
"""

import warnings

# macOS system Python links an old LibreSSL, and urllib3 (pulled in by
# yfinance) warns about it on import. It does not affect the VIE. This runs
# here, not in __main__.py, because the package is imported first.
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from vie.cli import main  # noqa: E402

__all__ = ["main"]
