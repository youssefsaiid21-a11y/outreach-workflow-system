"""
P2-2 / deep audit HIGH-4 - failure retry ladder.

A failed handler must NOT pause the sequence on the first error. Instead
the dispatcher reschedules forward via RETRY_LADDER_HOURS, only pausing
after MAX_CONSECUTIVE_FAILURES consecutive failures. A successful run
(or any non-failure outcome) resets the counter.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from src.config import MAX_CONSECUTIVE_FAILURES, RETRY_LADDER_HOURS


def test_config_defaults():
    """Sanity check that config exposes the expected ladder shape."""
    assert isinstance(RETRY_LADDER_HOURS, list)
    assert len(RETRY_LADDER_HOURS) >= 1
    assert MAX_CONSECUTIVE_FAILURES >= len(RETRY_LADDER_HOURS) + 1, \
        "MAX_CONSECUTIVE_FAILURES should be at least one more than the ladder length"
