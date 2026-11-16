"""
Tests for the Apollo reveal cache — the system-wide credit-saving layer.

All tests use mocked Apollo API calls (no real credits spent). Tests verify
that reveal_with_cache() correctly reads/writes the cache, deduplicates
IDs, handles failures gracefully, and logs credits only for new reveals.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("APOLLO_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base
from src.db.models import ApolloRevealCache, Prospect
from src.services.apollo import (
    _sanitize_json,
    reveal_with_cache,
    _extract_email,
    backfill_reveal_cache,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


def _make_person(apollo_id, email="test@example.com", first="John", last="Doe"):
    return {
        "id": apollo_id,
        "email": email,
        "first_name": first,
        "last_name": last,
        "title": "VP Sales",
        "organization": {"name": "Acme Corp"},
        "linkedin_url": f"https://linkedin.com/in/{first.lower()}",
    }


# ── _sanitize_json ──────────────────────────────────────────────────────────

def test_sanitize_json_handles_normal_dict():
    d = {"id": "abc", "email": "a@b.com", "nested": {"key": "val"}}
    assert _sanitize_json(d) == d


def test_sanitize_json_converts_datetime():
    dt = datetime(2026, 5, 5, 10, 0, 0)
    result = _sanitize_json({"ts": dt})
    assert isinstance(result["ts"], str)
    assert "2026" in result["ts"]


def test_sanitize_json_handles_none():
    result = _sanitize_json({"a": None, "b": [None, 1]})
    assert result == {"a": None, "b": [None, 1]}


# ── reveal_with_cache ───────────────────────────────────────────────────────

def test_all_miss(session_factory):
    """All IDs are new — reveal is called, all results cached."""
    ids = ["id1", "id2", "id3"]
    persons = [_make_person(i, f"{i}@test.com") for i in ids]

    mock_client = MagicMock()
    mock_client._reveal_people.return_value = persons

    result = reveal_with_cache(mock_client, ids, session_factory)

    mock_client._reveal_people.assert_called_once_with(ids)
    assert len(result) == 3

    # Verify cache was written
    db = session_factory()
    cached = db.query(ApolloRevealCache).all()
    assert len(cached) == 3
    db.close()


def test_all_hit(session_factory):
    """All IDs pre-cached — reveal is NOT called, 0 credits."""
    ids = ["id1", "id2"]

    # Pre-populate cache
    db = session_factory()
    for i in ids:
        db.add(ApolloRevealCache(apollo_id=i, email=f"{i}@test.com", raw_json=_make_person(i, f"{i}@test.com")))
    db.commit()
    db.close()

    mock_client = MagicMock()

    result = reveal_with_cache(mock_client, ids, session_factory)

    mock_client._reveal_people.assert_not_called()
    assert len(result) == 2


def test_partial_hit(session_factory):
    """Some IDs cached, some not — reveal called only for misses."""
    # Cache id1
    db = session_factory()
    db.add(ApolloRevealCache(apollo_id="id1", email="id1@test.com", raw_json=_make_person("id1", "id1@test.com")))
    db.commit()
    db.close()

    mock_client = MagicMock()
    mock_client._reveal_people.return_value = [_make_person("id2", "id2@test.com"), _make_person("id3", "id3@test.com")]

    result = reveal_with_cache(mock_client, ["id1", "id2", "id3"], session_factory)

    # Only id2 and id3 should be revealed
    mock_client._reveal_people.assert_called_once_with(["id2", "id3"])
    assert len(result) == 3  # 1 cached + 2 revealed


def test_bad_json_survives(session_factory):
    """One person has non-serializable value — others still cached, all returned."""
    ids = ["id1", "id2"]
    person1 = _make_person("id1", "id1@test.com")
    person2 = _make_person("id2", "id2@test.com")
    person2["weird_field"] = datetime(2026, 1, 1)  # non-serializable in some paths

    mock_client = MagicMock()
    mock_client._reveal_people.return_value = [person1, person2]

    result = reveal_with_cache(mock_client, ids, session_factory)

    # Both should be returned even if cache write had issues
    assert len(result) == 2

    # At least person1 should be cached (person2 might or might not depending on sanitization)
    db = session_factory()
    cached = db.query(ApolloRevealCache).all()
    assert len(cached) >= 1
    db.close()


def test_cache_failure_does_not_block_return(session_factory):
    """If session.commit raises, results are still returned."""
    ids = ["id1"]
    mock_client = MagicMock()
    mock_client._reveal_people.return_value = [_make_person("id1", "id1@test.com")]

    # Make the cache write session factory return a session that fails on commit
    call_count = [0]
    real_factory = session_factory

    def failing_factory():
        call_count[0] += 1
        if call_count[0] > 1:  # First call is the cache read, let it succeed
            sess = real_factory()
            original_commit = sess.commit
            def bad_commit():
                raise RuntimeError("DB is down")
            sess.commit = bad_commit
            return sess
        return real_factory()

    result = reveal_with_cache(mock_client, ids, failing_factory)

    # Result should still be returned even though cache write failed
    assert len(result) == 1
    assert result[0]["id"] == "id1"


def test_duplicate_ids_deduped(session_factory):
    """Duplicate IDs in input are deduped before reveal."""
    mock_client = MagicMock()
    mock_client._reveal_people.return_value = [_make_person("id1", "id1@test.com")]

    result = reveal_with_cache(mock_client, ["id1", "id1", "id1"], session_factory)

    # Should only reveal once
    mock_client._reveal_people.assert_called_once_with(["id1"])
    assert len(result) == 1


def test_empty_input(session_factory):
    """Empty list — no API calls, empty return."""
    mock_client = MagicMock()

    result = reveal_with_cache(mock_client, [], session_factory)

    mock_client._reveal_people.assert_not_called()
    assert result == []


def test_merge_handles_duplicate_pk(session_factory):
    """Revealing the same ID twice — merge upserts without error."""
    mock_client = MagicMock()
    person = _make_person("id1", "id1@test.com")
    mock_client._reveal_people.return_value = [person]

    # First reveal
    result1 = reveal_with_cache(mock_client, ["id1"], session_factory)
    assert len(result1) == 1

    # Second reveal of same ID (cache hit)
    mock_client._reveal_people.reset_mock()
    result2 = reveal_with_cache(mock_client, ["id1"], session_factory)
    assert len(result2) == 1
    mock_client._reveal_people.assert_not_called()  # Should be cache hit


# ── backfill_reveal_cache ────────────────────────────────────────────────────

def test_backfill_from_prospects(session_factory):
    """Backfill creates cache entries for existing prospects."""
    db = session_factory()
    for i in range(5):
        db.add(Prospect(
            id=str(uuid.uuid4()),
            apollo_id=f"apollo_{i}",
            email=f"p{i}@test.com",
            first_name=f"Person{i}",
            last_name="Test",
            status="new",
        ))
    db.commit()
    db.close()

    count = backfill_reveal_cache(session_factory)
    assert count == 5

    # Verify cache
    db = session_factory()
    cached = db.query(ApolloRevealCache).all()
    assert len(cached) == 5
    db.close()

    # Running again should backfill 0 (already cached)
    count2 = backfill_reveal_cache(session_factory)
    assert count2 == 0


# ── _extract_email ───────────────────────────────────────────────────────────

def test_extract_email_direct():
    assert _extract_email({"email": "a@b.com"}) == "a@b.com"


def test_extract_email_contact_emails():
    assert _extract_email({"contact_emails": [{"email": "x@y.com"}]}) == "x@y.com"


def test_extract_email_none():
    assert _extract_email({}) is None
    assert _extract_email({"email": ""}) is None
