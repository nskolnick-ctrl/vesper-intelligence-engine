"""Entry point for ``python3 -m vie``."""

import sys
import warnings

# macOS system Python links an old LibreSSL, and urllib3 (pulled in by
# yfinance) warns about it on every import. It does not affect the VIE, so
# hide it before anything imports urllib3.
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from vie.cli import main  # noqa: E402

sys.exit(main())
