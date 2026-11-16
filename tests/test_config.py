"""
Tests for centralized configuration.
"""

import pytest
from src.config import (
    MODEL_FAST,
    MODEL_SMART,
    MODEL_PRICING,
    DEFAULT_MODEL_PRICING,
    get_model_pricing,
    INVALID_EMAIL_STATUSES,
    VALID_EMAIL_STATUSES,
    UNCERTAIN_EMAIL_STATUSES,
    EMAIL_WORD_LIMITS,
    PROSPECT_CAP,
    TERMINAL_PROSPECT_STATUSES,
)


class TestModelConfig:
    def test_model_ids_are_strings(self):
        assert isinstance(MODEL_FAST, str)
        assert isinstance(MODEL_SMART, str)

    def test_model_ids_not_empty(self):
        assert len(MODEL_FAST) > 0
        assert len(MODEL_SMART) > 0

    def test_pricing_has_both_models(self):
        assert MODEL_SMART in MODEL_PRICING
        assert MODEL_FAST in MODEL_PRICING

    def test_pricing_structure(self):
        for model, prices in MODEL_PRICING.items():
            assert "input" in prices
            assert "output" in prices
            assert prices["input"] > 0
            assert prices["output"] > 0

    def test_get_model_pricing_known(self):
        prices = get_model_pricing(MODEL_SMART)
        assert prices["input"] > 0
        assert prices["output"] > 0

    def test_get_model_pricing_unknown(self):
        prices = get_model_pricing("some-future-model")
        assert prices == DEFAULT_MODEL_PRICING


class TestEmailStatusSets:
    def test_no_overlap_valid_invalid(self):
        assert VALID_EMAIL_STATUSES.isdisjoint(INVALID_EMAIL_STATUSES)

    def test_no_overlap_valid_uncertain(self):
        assert VALID_EMAIL_STATUSES.isdisjoint(UNCERTAIN_EMAIL_STATUSES)

    def test_no_overlap_invalid_uncertain(self):
        assert INVALID_EMAIL_STATUSES.isdisjoint(UNCERTAIN_EMAIL_STATUSES)

    def test_invalid_includes_dangerous(self):
        assert "spamtrap" in INVALID_EMAIL_STATUSES
        assert "abuse" in INVALID_EMAIL_STATUSES
        assert "do_not_mail" in INVALID_EMAIL_STATUSES


class TestEmailWordLimits:
    def test_has_three_emails(self):
        assert "email1" in EMAIL_WORD_LIMITS
        assert "email2" in EMAIL_WORD_LIMITS
        assert "email3" in EMAIL_WORD_LIMITS

    def test_email1_longest(self):
        assert EMAIL_WORD_LIMITS["email1"] > EMAIL_WORD_LIMITS["email2"]
        assert EMAIL_WORD_LIMITS["email1"] > EMAIL_WORD_LIMITS["email3"]

    def test_limits_are_positive(self):
        for key, limit in EMAIL_WORD_LIMITS.items():
            assert limit > 0


class TestProspectConfig:
    def test_cap_is_positive(self):
        assert PROSPECT_CAP > 0

    def test_terminal_statuses_exist(self):
        assert "enrolled" in TERMINAL_PROSPECT_STATUSES
        assert "replied" in TERMINAL_PROSPECT_STATUSES
        assert "bounced" in TERMINAL_PROSPECT_STATUSES
