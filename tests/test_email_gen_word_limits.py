"""
P4-3 - ``_word_limits_for_steps`` returns N per-step limits for an
N-step sequence. Previously hardcoded to 6 elements, which silently
truncated the word-limit hints for sequences with 7+ emails.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from src.services.email_gen_helpers import _word_limits_for_steps


def test_returns_empty_list_for_zero_or_negative():
    assert _word_limits_for_steps(0) == []
    assert _word_limits_for_steps(-3) == []


def test_single_step_is_100_words():
    assert _word_limits_for_steps(1) == [100]


def test_six_step_matches_legacy_default():
    # Legacy hardcoded list was [100, 60, 60, 60, 50, 50]
    assert _word_limits_for_steps(6) == [100, 60, 60, 60, 50, 50]


def test_eight_step_extends_with_50_word_tail():
    limits = _word_limits_for_steps(8)
    assert len(limits) == 8
    assert limits[0] == 100
    # Steps 2-4 stay at 60, then 50 for every subsequent step.
    assert limits[1:4] == [60, 60, 60]
    assert all(w == 50 for w in limits[4:])


def test_twelve_step_returns_twelve_values():
    limits = _word_limits_for_steps(12)
    assert len(limits) == 12
    assert limits[0] == 100
    assert limits[-1] == 50
