"""VIE Layer 4: Command-line interface.

Run from the repository root:

    python3 -m vie AAPL

This is the only layer allowed to catch every exception (Day 1 architecture),
so the user sees an informative message and a non-zero exit code, never a
Python traceback.
"""

from vie.cli import main

__all__ = ["main"]
