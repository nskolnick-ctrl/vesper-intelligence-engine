"""Pytest configuration for the Vesper Intelligence Engine.

Placing this file at the repository root puts the root on ``sys.path`` when
pytest runs, so ``pytest tests/`` resolves ``from src.data import ...`` without
the project needing to be installed first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
