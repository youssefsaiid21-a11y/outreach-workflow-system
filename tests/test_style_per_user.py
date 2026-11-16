"""P3-6: Per-user StyleProfile isolation.

Each rep's generated emails should pull from THEIR synced style profile.
The lookup must never fall back to another user's profile.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base
from src.db.models import StyleProfile, User
from src.api.conference import _get_style_block
from src.api.style import _get_latest_profile_model


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def two_users_with_profiles(db):
    user_a = User(
        id="ua", name="Alice", email="a@x.com",
        password_hash="hash", role="rep",
    )
    user_b = User(
        id="ub", name="Bob", email="b@x.com",
        password_hash="hash", role="rep",
    )
    db.add_all([user_a, user_b])

    prof_a = StyleProfile(
        id=str(uuid.uuid4()),
        user_id="ua",
        avg_word_count=50,
        formality="casual",
        greeting_patterns=["Hey {name},"],
        sign_off_patterns=["Cheers, Alice"],
        raw_profile={
            "avg_word_count": 50,
            "avg_sentence_length": 8,
            "formality": "casual",
            "greeting_patterns": ["Hey {name},"],
            "sign_off_patterns": ["Cheers, Alice"],
            "uses_bullet_points": False,
            "sample_emails": [],
            "voice": "alice_voice",
        },
    )
    prof_b = StyleProfile(
        id=str(uuid.uuid4()),
        user_id="ub",
        avg_word_count=100,
        formality="formal",
        greeting_patterns=["Dear {name},"],
        sign_off_patterns=["Regards, Bob"],
        raw_profile={
            "avg_word_count": 100,
            "avg_sentence_length": 15,
            "formality": "formal",
            "greeting_patterns": ["Dear {name},"],
            "sign_off_patterns": ["Regards, Bob"],
            "uses_bullet_points": True,
            "sample_emails": [],
            "voice": "bob_voice",
        },
    )
    db.add_all([prof_a, prof_b])
    db.commit()
    return user_a, user_b


def test_get_latest_profile_is_per_user(db, two_users_with_profiles):
    ua, ub = two_users_with_profiles
    p_a = _get_latest_profile_model(db, ua)
    p_b = _get_latest_profile_model(db, ub)
    assert p_a is not None and p_a.user_id == "ua"
    assert p_b is not None and p_b.user_id == "ub"
    assert p_a.raw_profile.get("voice") == "alice_voice"
    assert p_b.raw_profile.get("voice") == "bob_voice"


def test_get_latest_profile_no_fallback_to_other_user(db):
    """If the caller has no profile, do NOT return another user's profile."""
    user_a = User(id="ua", name="Alice", email="a@x.com", password_hash="h", role="rep")
    user_b = User(id="ub", name="Bob", email="b@x.com", password_hash="h", role="rep")
    db.add_all([user_a, user_b])
    # Only user_b has a profile.
    db.add(StyleProfile(
        id=str(uuid.uuid4()),
        user_id="ub",
        raw_profile={"voice": "bob_only"},
    ))
    db.commit()

    p_a = _get_latest_profile_model(db, user_a)
    assert p_a is None  # Must NOT leak Bob's profile to Alice


def test_get_style_block_scoped_to_user(db, two_users_with_profiles):
    block_a = _get_style_block(db, user_id="ua")
    block_b = _get_style_block(db, user_id="ub")
    # Different users should yield different style blocks.
    assert block_a != block_b
    # User A's block should not mention Bob's sign-off and vice versa.
    assert "Alice" in block_a or "Cheers" in block_a
    assert "Bob" in block_b or "Regards" in block_b
    assert "Bob" not in block_a and "Regards" not in block_a
    assert "Alice" not in block_b and "Cheers" not in block_b


def test_get_style_block_empty_for_user_with_no_profile(db):
    user_a = User(id="ua", name="Alice", email="a@x.com", password_hash="h", role="rep")
    user_b = User(id="ub", name="Bob", email="b@x.com", password_hash="h", role="rep")
    db.add_all([user_a, user_b])
    db.add(StyleProfile(
        id=str(uuid.uuid4()),
        user_id="ub",
        raw_profile={"voice": "bob_only"},
    ))
    db.commit()

    # User A has no profile and no writing_style setting → empty string.
    block = _get_style_block(db, user_id="ua")
    assert block == ""
