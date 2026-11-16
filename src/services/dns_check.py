"""
DNS verification for sending domains.

Checks SPF, DKIM, and DMARC records to warn before campaigns launch
from a misconfigured domain. Uses stdlib socket + dns TXT lookups
(no external dependency).
"""

import logging
import re
import socket
from typing import Optional

logger = logging.getLogger(__name__)


def _txt_lookup(domain: str, _retries: int = 2) -> list[str]:
    """Resolve TXT records for a domain using socket. Returns list of record strings.

    Audit 2026-05-05 #33: retries on transient DNS failures so a single
    glitch doesn't incorrectly mark a domain as misconfigured.
    """
    for attempt in range(_retries + 1):
        try:
            import dns.resolver
            answers = dns.resolver.resolve(domain, "TXT")
            return [b"".join(rdata.strings).decode("utf-8", errors="replace") for rdata in answers]
        except ImportError:
            # Fallback: shell out to nslookup (works on Windows without dnspython)
            import subprocess
            try:
                result = subprocess.run(
                    ["nslookup", "-type=TXT", domain],
                    capture_output=True, text=True, timeout=10,
                )
                # Parse nslookup output for quoted TXT values
                records = re.findall(r'"([^"]+)"', result.stdout)
                return records
            except Exception as e:
                logger.warning("nslookup fallback failed for %s: %s", domain, e)
                return []
        except Exception as e:
            if attempt < _retries:
                import time
                time.sleep(1)
                continue
            logger.warning("DNS TXT lookup failed for %s after %d attempts: %s", domain, _retries + 1, e)
            return []


def check_spf(domain: str) -> dict:
    """Check if SPF record exists and is valid."""
    records = _txt_lookup(domain)
    spf_records = [r for r in records if r.startswith("v=spf1")]

    if not spf_records:
        return {"status": "missing", "detail": "No SPF record found", "record": None}

    if len(spf_records) > 1:
        return {
            "status": "warning",
            "detail": f"Multiple SPF records found ({len(spf_records)})  -  this causes failures",
            "record": spf_records[0],
        }

    spf = spf_records[0]
    if "~all" in spf or "-all" in spf:
        return {"status": "ok", "detail": "SPF record found and enforced", "record": spf}

    if "?all" in spf or "+all" in spf:
        return {
            "status": "warning",
            "detail": "SPF record exists but is not enforced (?all or +all)",
            "record": spf,
        }

    return {"status": "ok", "detail": "SPF record found", "record": spf}


def check_dkim(domain: str, selector: str = "default") -> dict:
    """
    Check if a DKIM record exists for the given selector.
    Common selectors: default, google, selector1, selector2, k1.
    """
    dkim_domain = f"{selector}._domainkey.{domain}"
    records = _txt_lookup(dkim_domain)

    if not records:
        # Try common selectors before giving up
        common_selectors = ["google", "selector1", "selector2", "k1", "s1", "mail"]
        for alt in common_selectors:
            if alt == selector:
                continue
            alt_domain = f"{alt}._domainkey.{domain}"
            alt_records = _txt_lookup(alt_domain)
            if alt_records:
                dkim = alt_records[0]
                return {
                    "status": "ok",
                    "detail": f"DKIM found on selector '{alt}'",
                    "record": dkim[:200],
                    "selector": alt,
                }

        return {
            "status": "missing",
            "detail": f"No DKIM record found (tried {selector} + common selectors)",
            "record": None,
            "selector": None,
        }

    dkim = records[0]
    if "p=" in dkim:
        return {
            "status": "ok",
            "detail": f"DKIM record found on selector '{selector}'",
            "record": dkim[:200],
            "selector": selector,
        }

    return {
        "status": "warning",
        "detail": "DKIM record exists but may be incomplete (no p= key)",
        "record": dkim[:200],
        "selector": selector,
    }


def check_dmarc(domain: str) -> dict:
    """Check DMARC policy."""
    dmarc_domain = f"_dmarc.{domain}"
    records = _txt_lookup(dmarc_domain)
    dmarc_records = [r for r in records if r.startswith("v=DMARC1")]

    if not dmarc_records:
        return {"status": "missing", "detail": "No DMARC record found", "record": None}

    dmarc = dmarc_records[0]

    # Check policy
    policy_match = re.search(r"p=(\w+)", dmarc)
    policy = policy_match.group(1) if policy_match else "none"

    if policy == "none":
        return {
            "status": "warning",
            "detail": "DMARC exists but policy is 'none' (monitoring only, no enforcement)",
            "record": dmarc,
        }

    if policy in ("quarantine", "reject"):
        return {"status": "ok", "detail": f"DMARC policy: {policy}", "record": dmarc}

    return {"status": "ok", "detail": f"DMARC record found (policy: {policy})", "record": dmarc}


def check_domain(domain: str, dkim_selector: str = "default") -> dict:
    """
    Full DNS health check for a sending domain.

    Returns:
        {
            "domain": str,
            "overall": "ok" | "warning" | "fail",
            "spf": {...},
            "dkim": {...},
            "dmarc": {...},
            "issues": [str],
        }
    """
    spf = check_spf(domain)
    dkim = check_dkim(domain, selector=dkim_selector)
    dmarc = check_dmarc(domain)

    issues = []
    if spf["status"] == "missing":
        issues.append("SPF record is missing  -  emails will likely land in spam")
    elif spf["status"] == "warning":
        issues.append(f"SPF issue: {spf['detail']}")

    if dkim["status"] == "missing":
        issues.append("DKIM is not configured  -  reduces deliverability")
    elif dkim["status"] == "warning":
        issues.append(f"DKIM issue: {dkim['detail']}")

    if dmarc["status"] == "missing":
        issues.append("DMARC record is missing  -  domain is vulnerable to spoofing")
    elif dmarc["status"] == "warning":
        issues.append(f"DMARC issue: {dmarc['detail']}")

    # Overall assessment
    statuses = [spf["status"], dkim["status"], dmarc["status"]]
    if "missing" in statuses:
        overall = "fail"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "domain": domain,
        "overall": overall,
        "spf": spf,
        "dkim": dkim,
        "dmarc": dmarc,
        "issues": issues,
    }


def extract_domain(email: str) -> Optional[str]:
    """Extract domain from an email address."""
    if "@" in email:
        return email.split("@", 1)[1].strip().lower()
    return None
