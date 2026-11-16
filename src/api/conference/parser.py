"""
Conference lead spreadsheet parser.

Reads CSV / XLSX uploads, fuzzy-matches column headers, and emits a list
of normalised lead dicts ready for ``ConferenceLead`` row construction.

P4-1: extracted from the old monolith ``conference.py``. Pure functions
only  -  no DB session, no FastAPI dependencies, no auth. The only side
effect is the module logger. This is what makes the parser unit-testable
in isolation (P4-5).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re

import openpyxl

logger = logging.getLogger(__name__)


# ── Fuzzy column matching ────────────────────────────────────────────────────
# Each field maps to keywords that can appear *anywhere* in the header text.
# Matching is case-insensitive and checks for substring containment, so
# "Email Address", "Contact Email", "E-Mail" all match "email".
_COL_KEYWORDS = {
    "email":           ["email", "e-mail", "mail"],
    "first_name":      ["first name", "first_name", "firstname", "given name", "prenom"],
    "last_name":       ["last name", "last_name", "lastname", "surname", "family name", "nom"],
    "company":         ["company", "organisation", "organization", "employer", "firm", "business"],
    "title":           ["title", "job title", "position", "role", "designation", "job role"],
    "notes":           ["note", "comment", "context", "description", "remark", "memo", "info", "detail"],
    "linkedin_url":    ["linkedin", "profile url", "profile link", "li url"],
    "company_website": ["website", "web", "url", "domain", "homepage"],
    "cc":              ["cc", "copy", "carbon copy"],
    "category":        ["category", "type", "lead type", "contact type", "classification", "segment"],
}


def _map_columns(header: list[str]) -> dict:
    """Return ``{field: column_index}`` using fuzzy substring matching on headers."""
    col_map: dict[str, int] = {}
    used_indices: set[int] = set()
    # First pass: fuzzy keyword matching
    for field, keywords in _COL_KEYWORDS.items():
        for i, h in enumerate(header):
            if i in used_indices:
                continue
            h_lower = h.lower().strip()
            if any(kw in h_lower for kw in keywords):
                col_map[field] = i
                used_indices.add(i)
                break
    return col_map


def _detect_email_column_by_content(header: list[str], rows: list) -> int | None:
    """If header matching fails, scan the first few rows to find a column with @ signs."""
    sample = rows[:min(10, len(rows))]
    best_col, best_score = None, 0
    for col_idx in range(len(header)):
        hits = sum(
            1 for row in sample
            if col_idx < len(row) and row[col_idx] and "@" in str(row[col_idx])
        )
        if hits > best_score:
            best_score = hits
            best_col = col_idx
    # Require at least 50% of sampled rows to have an @ sign
    if best_col is not None and best_score >= max(1, len(sample) // 2):
        return best_col
    return None


def _detect_name_from_full_name(header: list[str]) -> dict:
    """Detect a 'full name' or 'name' column and split it into first/last."""
    for i, h in enumerate(header):
        h_lower = h.lower().strip()
        if h_lower in (
            "name", "full name", "full_name", "fullname",
            "contact", "contact name", "attendee", "delegate",
        ):
            return {"_full_name": i}
    return {}


def _rows_to_leads(col_map: dict, header: list[str], rows: list) -> list[dict]:
    """Convert raw rows to lead dicts using ``col_map``.

    Intelligently handles:

    - Missing email column (auto-detect by @ content)
    - Full name column (split into first/last)
    - Any extra columns captured as ``raw_data`` for AI context
    - CC field preserved in raw_data
    """
    # If no email column found by header, try content detection
    if "email" not in col_map:
        detected = _detect_email_column_by_content(header, rows)
        if detected is not None:
            col_map["email"] = detected
            logger.info(
                "Auto-detected email column at index %d (%s)",
                detected, header[detected] if detected < len(header) else "?",
            )

    if "email" not in col_map:
        raise ValueError(
            "Could not find an email column. Make sure at least one column "
            "contains email addresses."
        )

    # If no first_name column, check for a combined name column
    if "first_name" not in col_map:
        name_map = _detect_name_from_full_name(header)
        col_map.update(name_map)

    leads: list[dict] = []
    for row in rows:
        if not any(cell is not None and str(cell).strip() for cell in row):
            continue
        raw_email = row[col_map["email"]] if col_map.get("email") is not None else None
        if not raw_email or "@" not in str(raw_email):
            continue
        # SWAT-M10: stricter email extraction — reject consecutive dots, leading/trailing dots
        email_matches = re.findall(
            r"[a-zA-Z0-9](?:[a-zA-Z0-9._%+\-]*[a-zA-Z0-9])?@[a-zA-Z0-9](?:[a-zA-Z0-9.\-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}",
            str(raw_email),
        )
        email = email_matches[0] if email_matches else None
        if not email or ".." in email:
            continue

        def get(field, _row=row):
            idx = col_map.get(field)
            val = _row[idx] if idx is not None and idx < len(_row) else None
            return str(val).strip() if val is not None and str(val).strip() else None

        # Handle full name → first/last split
        first_name = get("first_name")
        last_name = get("last_name")
        if not first_name and "_full_name" in col_map:
            full = get("_full_name")
            if full:
                parts = full.strip().split(None, 1)
                first_name = parts[0] if parts else None
                last_name = parts[1] if len(parts) > 1 else None

        # Build a dict of ALL columns for this row (for AI context)
        raw: dict[str, str] = {}
        for i, cell in enumerate(row):
            if i < len(header) and header[i]:
                val = str(cell).strip() if cell is not None else ""
                if val:
                    raw[header[i]] = val

        # Normalize category to lowercase
        cat = get("category")
        if cat:
            cat_lower = cat.strip().lower()
            if cat_lower in ("partner", "referral", "lead"):
                cat = cat_lower

        # Validate CC  -  only keep entries that look like email addresses
        raw_cc = get("cc")
        if raw_cc:
            cc_parts = [p.strip() for p in raw_cc.replace(";", ",").split(",")]
            cc_valid = [p for p in cc_parts if "@" in p and "." in p]
            raw_cc = ", ".join(cc_valid) if cc_valid else None

        leads.append({
            "email": str(email).strip(),
            "first_name": first_name,
            "last_name": last_name,
            "company": get("company"),
            "title": get("title"),
            "notes": get("notes"),
            "linkedin_url": get("linkedin_url"),
            "company_website": get("company_website"),
            "cc": raw_cc,
            "category": cat,
            "raw_data": json.dumps(raw) if raw else None,
        })
    return leads


def _is_header_row(row: list, data_rows: list) -> bool:
    """Heuristic: a header row has no @ signs and no mostly-numeric cells."""
    row_str = [str(c).strip() if c else "" for c in row]
    if any("@" in c for c in row_str):
        return False  # contains an email  -  this is data, not a header
    return True


def _smart_parse(raw_rows: list, source: str = "excel") -> list[dict]:
    """Intelligently parse rows from any spreadsheet format.

    Handles header rows, headerless sheets, messy column names, merged
    cells, and auto-detects email columns by content.
    """
    if not raw_rows:
        return []

    # Find first non-empty row
    start = 0
    for i, row in enumerate(raw_rows):
        cells = [c for c in row if c is not None and str(c).strip()]
        if cells:
            start = i
            break
    else:
        return []

    candidate_header = raw_rows[start]
    header = [str(c).strip().lower() if c else "" for c in candidate_header]
    data_rows = raw_rows[start + 1:]

    # Check if the first non-empty row is actually a header or data
    if _is_header_row(candidate_header, data_rows):
        col_map = _map_columns(header)
        return _rows_to_leads(col_map, header, data_rows)
    else:
        # First row is data  -  generate generic column names
        n_cols = len(candidate_header)
        header = [f"col{i}" for i in range(n_cols)]
        col_map: dict[str, int] = {}  # no header match  -  _rows_to_leads will auto-detect by content
        all_data = raw_rows[start:]  # include the first row as data
        logger.info("No header row detected  -  using content-based column detection")
        return _rows_to_leads(col_map, header, all_data)


def _parse_excel(content: bytes) -> list[dict]:
    """Parse an Excel file (.xlsx/.xls) and return a list of lead dicts."""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return _smart_parse(rows, source="excel")


def _parse_csv(content: bytes) -> list[dict]:
    """Parse a CSV file and return a list of lead dicts."""
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Could not decode CSV file. Try saving it as UTF-8.")

    reader = csv.reader(text.splitlines())
    rows = list(reader)
    # Convert empty strings to None for consistency with Excel parser
    rows = [[cell if cell.strip() else None for cell in row] for row in rows]
    return _smart_parse(rows, source="csv")
