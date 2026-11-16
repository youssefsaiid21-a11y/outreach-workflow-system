"""
Tests for DNS check service.

Tests the parsing/logic functions. Actual DNS lookups are mocked.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.services.dns_check import (
    check_spf,
    check_dkim,
    check_dmarc,
    check_domain,
    extract_domain,
)


# ── extract_domain ───────────────────────────────────────────────────────────

class TestExtractDomain:
    def test_normal_email(self):
        assert extract_domain("john@acme.com") == "acme.com"

    def test_uppercase(self):
        assert extract_domain("John@ACME.COM") == "acme.com"

    def test_no_at(self):
        assert extract_domain("not-an-email") is None

    def test_with_spaces(self):
        assert extract_domain(" john@acme.com ") == "acme.com"


# ── SPF checks ──────────────────────────────────────────────────────────────

class TestCheckSPF:
    @patch("src.services.dns_check._txt_lookup")
    def test_valid_spf_with_hardfail(self, mock_lookup):
        mock_lookup.return_value = ["v=spf1 include:_spf.google.com -all"]
        result = check_spf("example.com")
        assert result["status"] == "ok"

    @patch("src.services.dns_check._txt_lookup")
    def test_valid_spf_with_softfail(self, mock_lookup):
        mock_lookup.return_value = ["v=spf1 include:_spf.google.com ~all"]
        result = check_spf("example.com")
        assert result["status"] == "ok"

    @patch("src.services.dns_check._txt_lookup")
    def test_no_spf(self, mock_lookup):
        mock_lookup.return_value = []
        result = check_spf("example.com")
        assert result["status"] == "missing"

    @patch("src.services.dns_check._txt_lookup")
    def test_multiple_spf_warning(self, mock_lookup):
        mock_lookup.return_value = [
            "v=spf1 include:a.com -all",
            "v=spf1 include:b.com -all",
        ]
        result = check_spf("example.com")
        assert result["status"] == "warning"
        assert "Multiple" in result["detail"]

    @patch("src.services.dns_check._txt_lookup")
    def test_permissive_spf_warning(self, mock_lookup):
        mock_lookup.return_value = ["v=spf1 +all"]
        result = check_spf("example.com")
        assert result["status"] == "warning"

    @patch("src.services.dns_check._txt_lookup")
    def test_non_spf_records_ignored(self, mock_lookup):
        mock_lookup.return_value = [
            "google-site-verification=abc123",
            "v=spf1 include:sendgrid.net -all",
        ]
        result = check_spf("example.com")
        assert result["status"] == "ok"


# ── DKIM checks ─────────────────────────────────────────────────────────────

class TestCheckDKIM:
    @patch("src.services.dns_check._txt_lookup")
    def test_found_on_default_selector(self, mock_lookup):
        mock_lookup.return_value = ["v=DKIM1; k=rsa; p=MIIBIjAN..."]
        result = check_dkim("example.com", selector="default")
        assert result["status"] == "ok"
        assert result["selector"] == "default"

    @patch("src.services.dns_check._txt_lookup")
    def test_no_dkim(self, mock_lookup):
        mock_lookup.return_value = []
        result = check_dkim("example.com", selector="default")
        assert result["status"] == "missing"

    @patch("src.services.dns_check._txt_lookup")
    def test_found_on_alternate_selector(self, mock_lookup):
        call_count = [0]

        def side_effect(domain):
            call_count[0] += 1
            if "google._domainkey" in domain:
                return ["v=DKIM1; k=rsa; p=abc123"]
            return []

        mock_lookup.side_effect = side_effect
        result = check_dkim("example.com", selector="default")
        assert result["status"] == "ok"
        assert result["selector"] == "google"


# ── DMARC checks ────────────────────────────────────────────────────────────

class TestCheckDMARC:
    @patch("src.services.dns_check._txt_lookup")
    def test_reject_policy(self, mock_lookup):
        mock_lookup.return_value = ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"]
        result = check_dmarc("example.com")
        assert result["status"] == "ok"
        assert "reject" in result["detail"]

    @patch("src.services.dns_check._txt_lookup")
    def test_quarantine_policy(self, mock_lookup):
        mock_lookup.return_value = ["v=DMARC1; p=quarantine;"]
        result = check_dmarc("example.com")
        assert result["status"] == "ok"

    @patch("src.services.dns_check._txt_lookup")
    def test_none_policy_warning(self, mock_lookup):
        mock_lookup.return_value = ["v=DMARC1; p=none;"]
        result = check_dmarc("example.com")
        assert result["status"] == "warning"
        assert "monitoring only" in result["detail"]

    @patch("src.services.dns_check._txt_lookup")
    def test_no_dmarc(self, mock_lookup):
        mock_lookup.return_value = []
        result = check_dmarc("example.com")
        assert result["status"] == "missing"


# ── Full domain check ───────────────────────────────────────────────────────

class TestCheckDomain:
    @patch("src.services.dns_check._txt_lookup")
    def test_all_ok(self, mock_lookup):
        def side_effect(domain):
            if domain == "example.com":
                return ["v=spf1 include:google.com -all"]
            if "_domainkey" in domain:
                return ["v=DKIM1; k=rsa; p=abc"]
            if "_dmarc" in domain:
                return ["v=DMARC1; p=reject;"]
            return []

        mock_lookup.side_effect = side_effect
        result = check_domain("example.com")
        assert result["overall"] == "ok"
        assert result["domain"] == "example.com"
        assert len(result["issues"]) == 0

    @patch("src.services.dns_check._txt_lookup")
    def test_all_missing(self, mock_lookup):
        mock_lookup.return_value = []
        result = check_domain("example.com")
        assert result["overall"] == "fail"
        assert len(result["issues"]) == 3  # SPF, DKIM, DMARC all missing
