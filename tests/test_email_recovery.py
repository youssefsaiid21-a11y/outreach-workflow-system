"""
Tests for email recovery pattern detection and application.

Tests the pure pattern-matching logic without making any API calls.
"""

import pytest
from src.services.email_recovery import (
    _domain_from_email,
    _detect_pattern,
    _apply_pattern,
)


class TestDomainFromEmail:
    def test_normal_email(self):
        assert _domain_from_email("john@acme.com") == "acme.com"

    def test_with_whitespace(self):
        assert _domain_from_email("  john@acme.com  ") == "acme.com"

    def test_uppercase(self):
        assert _domain_from_email("John@ACME.COM") == "acme.com"

    def test_no_at_sign(self):
        assert _domain_from_email("not-an-email") == ""

    def test_empty(self):
        assert _domain_from_email("") == ""


class TestDetectPattern:
    def test_first_dot_last(self):
        assert _detect_pattern("John", "Doe", "john.doe") == "{first}.{last}"

    def test_first_last(self):
        assert _detect_pattern("John", "Doe", "johndoe") == "{first}{last}"

    def test_f_dot_last(self):
        assert _detect_pattern("John", "Doe", "j.doe") == "{f}.{last}"

    def test_f_last(self):
        assert _detect_pattern("John", "Doe", "jdoe") == "{f}{last}"

    def test_first_only(self):
        assert _detect_pattern("John", "Doe", "john") == "{first}"

    def test_last_dot_first(self):
        assert _detect_pattern("John", "Doe", "doe.john") == "{last}.{first}"

    def test_last_first(self):
        assert _detect_pattern("John", "Doe", "doejohn") == "{last}{first}"

    def test_last_only(self):
        assert _detect_pattern("John", "Doe", "doe") == "{last}"

    def test_no_match(self):
        assert _detect_pattern("John", "Doe", "sales") is None

    def test_empty_first(self):
        assert _detect_pattern("", "Doe", "doe") is None

    def test_empty_last(self):
        assert _detect_pattern("John", "", "john") is None


class TestApplyPattern:
    def test_first_dot_last(self):
        assert _apply_pattern("{first}.{last}", "Sarah", "Connor") == "sarah.connor"

    def test_first_last(self):
        assert _apply_pattern("{first}{last}", "Sarah", "Connor") == "sarahconnor"

    def test_f_dot_last(self):
        assert _apply_pattern("{f}.{last}", "Sarah", "Connor") == "s.connor"

    def test_f_last(self):
        assert _apply_pattern("{f}{last}", "Sarah", "Connor") == "sconnor"

    def test_first_only(self):
        assert _apply_pattern("{first}", "Sarah", "Connor") == "sarah"

    def test_unknown_pattern(self):
        assert _apply_pattern("{unknown}", "Sarah", "Connor") is None

    def test_empty_name(self):
        assert _apply_pattern("{first}.{last}", "", "Connor") is None

    def test_whitespace_handling(self):
        assert _apply_pattern("{first}.{last}", "  Sarah  ", "  Connor  ") == "sarah.connor"

    def test_all_patterns_roundtrip(self):
        """Every pattern detected by _detect_pattern should be reconstructable by _apply_pattern."""
        test_cases = [
            ("John", "Doe", "john.doe"),
            ("John", "Doe", "johndoe"),
            ("John", "Doe", "j.doe"),
            ("John", "Doe", "jdoe"),
            ("John", "Doe", "john"),
            ("John", "Doe", "doe.john"),
            ("John", "Doe", "doejohn"),
            ("John", "Doe", "doe"),
        ]
        for first, last, local in test_cases:
            pattern = _detect_pattern(first, last, local)
            assert pattern is not None, f"No pattern detected for {local}"
            reconstructed = _apply_pattern(pattern, first, last)
            assert reconstructed == local, (
                f"Roundtrip failed: pattern={pattern}, expected={local}, got={reconstructed}"
            )
