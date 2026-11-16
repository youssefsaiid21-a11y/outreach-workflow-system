"""
P4-5 - pure-function tests for ``src.api.conference.parser``.

The parser module has zero runtime dependencies on the DB or FastAPI, so
these tests can import it directly and exercise every edge case without
spinning up an app or a session. Covers:

- ``_smart_parse`` happy path + leading-blank-row tolerance
- ``_map_columns`` fuzzy header matching (First Name / Email Address /
  Job Title / Company Name / LinkedIn URL, etc.)
- ``_detect_email_column_by_content`` fallback when no email-like header
- ``_detect_name_from_full_name`` splitting "John Smith" → first/last
- ``_rows_to_leads`` conversion from mapped rows
- ``_is_header_row`` heuristic
- ``_parse_csv`` / ``_parse_excel`` round-trip
- Empty-file and blank-column edge cases
- Embedded-comma CSV field
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import openpyxl
import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from src.api.conference.parser import (
    _detect_email_column_by_content,
    _detect_name_from_full_name,
    _is_header_row,
    _map_columns,
    _parse_csv,
    _parse_excel,
    _rows_to_leads,
    _smart_parse,
)


# ── _map_columns ────────────────────────────────────────────────────────


def test_map_columns_matches_common_header_variants():
    header = [
        "First Name",
        "Last Name",
        "Email Address",
        "Job Title",
        "Company Name",
        "LinkedIn URL",
    ]
    col_map = _map_columns([h.lower() for h in header])
    assert col_map["first_name"] == 0
    assert col_map["last_name"] == 1
    assert col_map["email"] == 2
    assert col_map["title"] == 3
    assert col_map["company"] == 4
    assert col_map["linkedin_url"] == 5


def test_map_columns_handles_snake_case_and_givenname():
    header = ["given name", "surname", "e-mail", "role", "organisation"]
    col_map = _map_columns(header)
    assert col_map["first_name"] == 0
    assert col_map["last_name"] == 1
    assert col_map["email"] == 2
    assert col_map["title"] == 3
    assert col_map["company"] == 4


def test_map_columns_ignores_unknown_headers():
    header = ["id", "timestamp", "email", "notes"]
    col_map = _map_columns(header)
    assert col_map["email"] == 2
    assert col_map["notes"] == 3
    assert "first_name" not in col_map


# ── _detect_email_column_by_content ────────────────────────────────────


def test_detect_email_column_by_content_scores_the_at_column():
    header = ["id", "name", "contact", "status"]
    rows = [
        ["1", "Alice", "alice@example.com", "active"],
        ["2", "Bob", "bob@example.com", "active"],
        ["3", "Carol", "carol@example.com", "active"],
    ]
    idx = _detect_email_column_by_content(header, rows)
    assert idx == 2


def test_detect_email_column_returns_none_when_no_at_signs():
    header = ["id", "name", "notes"]
    rows = [["1", "Alice", "nothing"], ["2", "Bob", "still nothing"]]
    assert _detect_email_column_by_content(header, rows) is None


# ── _detect_name_from_full_name ────────────────────────────────────────


def test_detect_name_from_full_name_finds_fullname_column():
    assert _detect_name_from_full_name(["full name", "email"]) == {"_full_name": 0}
    assert _detect_name_from_full_name(["attendee", "email"]) == {"_full_name": 0}
    assert _detect_name_from_full_name(["delegate", "email"]) == {"_full_name": 0}


def test_detect_name_from_full_name_ignores_when_absent():
    assert _detect_name_from_full_name(["first_name", "last_name", "email"]) == {}


# ── _rows_to_leads ─────────────────────────────────────────────────────


def test_rows_to_leads_builds_lead_dicts():
    header = ["first name", "last name", "email", "company"]
    rows = [
        ["John", "Smith", "john@acme.com", "Acme Corp"],
        ["Jane", "Doe", "jane@beta.com", "Beta LLC"],
    ]
    col_map = _map_columns(header)
    leads = _rows_to_leads(col_map, header, rows)
    assert len(leads) == 2
    assert leads[0]["email"] == "john@acme.com"
    assert leads[0]["first_name"] == "John"
    assert leads[0]["last_name"] == "Smith"
    assert leads[0]["company"] == "Acme Corp"
    assert leads[1]["email"] == "jane@beta.com"


def test_rows_to_leads_splits_full_name_when_first_name_missing():
    header = ["full name", "email"]
    rows = [["John Smith", "john@example.com"]]
    col_map = _map_columns(header)
    # _map_columns by itself doesn't pick up "full name", but _rows_to_leads
    # calls _detect_name_from_full_name internally.
    leads = _rows_to_leads(col_map, header, rows)
    assert len(leads) == 1
    assert leads[0]["first_name"] == "John"
    assert leads[0]["last_name"] == "Smith"


def test_rows_to_leads_auto_detects_email_column():
    header = ["id", "name", "contact"]
    rows = [
        ["1", "Alice", "alice@example.com"],
        ["2", "Bob", "bob@example.com"],
    ]
    col_map = _map_columns(header)  # no "email" match in header
    leads = _rows_to_leads(col_map, header, rows)
    assert len(leads) == 2
    assert leads[0]["email"] == "alice@example.com"


def test_rows_to_leads_raises_when_no_email_detectable():
    header = ["id", "city"]
    rows = [["1", "Dubai"], ["2", "Riyadh"]]
    col_map = _map_columns(header)
    with pytest.raises(ValueError, match="email column"):
        _rows_to_leads(col_map, header, rows)


def test_rows_to_leads_extracts_clean_email_from_messy_cell():
    header = ["contact"]
    rows = [["+6012 877 8942 | user@domain.com"]] * 3  # needs enough rows to trigger content detection
    col_map = {}  # force content-detection path
    leads = _rows_to_leads(col_map, header, rows)
    assert len(leads) == 3
    assert leads[0]["email"] == "user@domain.com"


# ── _is_header_row ──────────────────────────────────────────────────────


def test_is_header_row_rejects_rows_with_at_sign():
    assert _is_header_row(["alice@example.com", "Alice"], []) is False


def test_is_header_row_accepts_label_row():
    assert _is_header_row(["Name", "Email", "Company"], []) is True


# ── _smart_parse ────────────────────────────────────────────────────────


def test_smart_parse_happy_path():
    rows = [
        ["First Name", "Last Name", "Email", "Company"],
        ["Alice", "Anderson", "alice@example.com", "Acme"],
        ["Bob", "Brown", "bob@example.com", "Beta"],
    ]
    leads = _smart_parse(rows)
    assert len(leads) == 2
    assert leads[0]["first_name"] == "Alice"
    assert leads[0]["email"] == "alice@example.com"


def test_smart_parse_skips_leading_blank_rows():
    rows = [
        [None, None, None],
        ["", "", ""],
        ["First Name", "Email", "Company"],
        ["Carol", "carol@example.com", "Gamma"],
    ]
    leads = _smart_parse(rows)
    assert len(leads) == 1
    assert leads[0]["email"] == "carol@example.com"


def test_smart_parse_headerless_sheet():
    # First row is actual data - the parser should content-detect the
    # email column and still produce a lead.
    rows = [
        ["Alice", "alice@example.com"],
        ["Bob", "bob@example.com"],
    ]
    leads = _smart_parse(rows)
    assert len(leads) == 2
    assert {l["email"] for l in leads} == {"alice@example.com", "bob@example.com"}


def test_smart_parse_empty_input_returns_empty_list():
    assert _smart_parse([]) == []
    assert _smart_parse([[None, None], ["", ""]]) == []


# ── _parse_csv ──────────────────────────────────────────────────────────


def test_parse_csv_basic_roundtrip():
    content = (
        "First Name,Last Name,Email,Company\n"
        "Alice,Anderson,alice@example.com,Acme\n"
        "Bob,Brown,bob@example.com,Beta\n"
    ).encode("utf-8")
    leads = _parse_csv(content)
    assert len(leads) == 2
    assert leads[0]["first_name"] == "Alice"
    assert leads[1]["email"] == "bob@example.com"


def test_parse_csv_handles_quoted_commas_in_field():
    content = (
        'Name,Email,Company\n'
        '"Smith, John",john@example.com,"Acme, Inc."\n'
    ).encode("utf-8")
    leads = _parse_csv(content)
    assert len(leads) == 1
    assert leads[0]["email"] == "john@example.com"
    assert leads[0]["company"] == "Acme, Inc."


def test_parse_csv_handles_utf8_bom():
    content = (
        b"\xef\xbb\xbffirst name,email\n"
        b"Layla,layla@example.com\n"
    )
    leads = _parse_csv(content)
    assert len(leads) == 1
    assert leads[0]["first_name"] == "Layla"


# ── _parse_excel ────────────────────────────────────────────────────────


def test_parse_excel_basic_roundtrip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name", "Last Name", "Email", "Company"])
    ws.append(["Alice", "Anderson", "alice@example.com", "Acme"])
    ws.append(["Bob", "Brown", "bob@example.com", "Beta"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    leads = _parse_excel(buf.getvalue())
    assert len(leads) == 2
    assert leads[0]["email"] == "alice@example.com"
    assert leads[1]["first_name"] == "Bob"
