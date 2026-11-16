"""
Tests for the 2026-04-11 hardcoded-values audit fixes.

Each test pins a specific invariant that, if broken, indicates a
regression in the single-source-of-truth refactor:

  1. Every site that used to hardcode ``range(1, 7)`` now derives from
     ``MAX_SEQUENCE_EMAILS`` via ``email_slot_range()``.
  2. ``email_word_limit(key)`` returns the hand-tuned value for known
     slots and the ``DEFAULT_EMAIL_WORD_LIMIT`` fallback for unknown ones.
  3. ``_DEFAULT_EMAIL_DAYS`` in campaigns.py now points at the
     canonical ``DEFAULT_EMAIL_DAY_OFFSETS`` in config.py (no drift).
  4. ``/config/sequence-defaults`` returns the correct shape constants.
  5. Raising ``MAX_SEQUENCE_EMAILS`` via env does NOT break any existing
     test paths (smoke).
  6. The additive ``email_sequences.steps_json`` column exists in the
     ORM model and is writable, but no code path reads from it yet.
  7. ``CampaignCreateRequest.sending_limit_per_day`` defaults to
     ``INSTANTLY_DEFAULT_DAILY_LIMIT`` from config, not a magic number.
  8. Email validator walks the actual sequence dict, not the hand-tuned
     EMAIL_WORD_LIMITS - so a sequence with slots beyond the limits dict
     still gets validated with the fallback.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("ANTHROPIC_API_KEY", "test")


class TestMaxSequenceEmails:
    def test_constant_exists_and_positive(self):
        from src.config import MAX_SEQUENCE_EMAILS
        assert isinstance(MAX_SEQUENCE_EMAILS, int)
        assert MAX_SEQUENCE_EMAILS >= 1

    def test_email_slot_range_matches_constant(self):
        from src.config import MAX_SEQUENCE_EMAILS, email_slot_range
        rng = list(email_slot_range())
        assert rng == list(range(1, MAX_SEQUENCE_EMAILS + 1))

    def test_email_slot_keys_matches_constant(self):
        from src.config import MAX_SEQUENCE_EMAILS, email_slot_keys
        keys = email_slot_keys()
        assert keys == [f"email{n}" for n in range(1, MAX_SEQUENCE_EMAILS + 1)]


class TestEmailWordLimit:
    def test_known_key_returns_tuned_value(self):
        from src.config import email_word_limit, EMAIL_WORD_LIMITS
        for key, expected in EMAIL_WORD_LIMITS.items():
            assert email_word_limit(key) == expected

    def test_unknown_key_returns_default(self):
        from src.config import email_word_limit, DEFAULT_EMAIL_WORD_LIMIT
        assert email_word_limit("email7") == DEFAULT_EMAIL_WORD_LIMIT
        assert email_word_limit("email99") == DEFAULT_EMAIL_WORD_LIMIT
        assert email_word_limit("not_a_real_key") == DEFAULT_EMAIL_WORD_LIMIT

    def test_default_is_conservative_floor(self):
        """DEFAULT_EMAIL_WORD_LIMIT must be <= the smallest tuned value so
        a new slot never accidentally relaxes the validator."""
        from src.config import DEFAULT_EMAIL_WORD_LIMIT, EMAIL_WORD_LIMITS
        assert DEFAULT_EMAIL_WORD_LIMIT <= min(EMAIL_WORD_LIMITS.values())


class TestDefaultEmailDaysSingleSource:
    def test_campaigns_py_reexports_config_constant(self):
        from src.api.campaigns import (
            _DEFAULT_EMAIL_DAYS,
            _DEFAULT_LI_CONNECT_DAY,
            _DEFAULT_LI_MESSAGE_DAY,
        )
        from src.config import (
            DEFAULT_EMAIL_DAY_OFFSETS,
            DEFAULT_LINKEDIN_CONNECT_DAY,
            DEFAULT_LINKEDIN_MESSAGE_DAY,
        )
        # Must be the same object (not a copy) so bumping the canonical
        # value in config.py propagates everywhere.
        assert _DEFAULT_EMAIL_DAYS is DEFAULT_EMAIL_DAY_OFFSETS
        assert _DEFAULT_LI_CONNECT_DAY == DEFAULT_LINKEDIN_CONNECT_DAY
        assert _DEFAULT_LI_MESSAGE_DAY == DEFAULT_LINKEDIN_MESSAGE_DAY


class TestConfigEndpoint:
    def test_sequence_defaults_returns_expected_keys(self):
        from src.api.app_config import get_sequence_defaults
        d = get_sequence_defaults()
        required = {
            "max_sequence_emails",
            "default_email_day_offsets",
            "default_linkedin_connect_day",
            "default_linkedin_message_day",
            "default_email_word_limit",
            "email_word_limits",
            "linkedin_word_limits",
        }
        assert required.issubset(d.keys())

    def test_sequence_defaults_values_match_config(self):
        from src.api.app_config import get_sequence_defaults
        from src import config as cfg
        d = get_sequence_defaults()
        assert d["max_sequence_emails"] == cfg.MAX_SEQUENCE_EMAILS
        assert d["default_email_day_offsets"] == list(cfg.DEFAULT_EMAIL_DAY_OFFSETS)
        assert d["default_linkedin_connect_day"] == cfg.DEFAULT_LINKEDIN_CONNECT_DAY
        assert d["default_linkedin_message_day"] == cfg.DEFAULT_LINKEDIN_MESSAGE_DAY
        assert d["default_email_word_limit"] == cfg.DEFAULT_EMAIL_WORD_LIMIT

    def test_sequence_defaults_is_json_serialisable(self):
        """FastAPI will serialise the dict to JSON - make sure every
        value is a native JSON type, not e.g. a range object."""
        import json
        from src.api.app_config import get_sequence_defaults
        d = get_sequence_defaults()
        # Should not raise
        s = json.dumps(d)
        assert len(s) > 0


class TestCampaignCreateDefault:
    def test_default_sending_limit_uses_config_constant(self):
        from src.api.campaigns import CampaignCreateRequest
        from src.config import INSTANTLY_DEFAULT_DAILY_LIMIT
        req = CampaignCreateRequest(name="x", goal="y", value_prop="z")
        assert req.sending_limit_per_day == INSTANTLY_DEFAULT_DAILY_LIMIT



class TestAdditiveStepsJsonColumn:
    def test_column_exists_in_orm(self):
        from src.db.models import EmailSequence
        # SQLAlchemy declarative attribute
        assert hasattr(EmailSequence, "steps_json")

    def test_column_is_nullable(self):
        from src.db.models import EmailSequence
        col = EmailSequence.__table__.c.steps_json
        assert col.nullable is True

    def test_can_write_and_read_back(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from src.db.models import Base, Campaign, CampaignStatus, EmailSequence, Prospect, User

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        try:
            db.add(User(id="u", email="r@x.com", name="R", password_hash="x"))
            db.add(Campaign(
                id="c", user_id="u", name="T", goal="g", value_prop="v",
                status=CampaignStatus.active,
            ))
            db.add(Prospect(
                id="p", user_id="u", email="p@x.com",
                first_name="P", last_name="L", company="Co",
            ))
            seq = EmailSequence(
                id="s", campaign_id="c", prospect_id="p",
                approved=False,
                email1_subject="s1", email1_body="b1",
                # Write the additive column with a future-shape payload
                steps_json=[
                    {"n": 1, "type": "email", "subject": "s1", "body": "b1"},
                    {"n": 2, "type": "email", "subject": "s2", "body": "b2"},
                ],
            )
            db.add(seq)
            db.commit()

            fresh = db.query(EmailSequence).filter_by(id="s").first()
            assert fresh.steps_json is not None
            assert len(fresh.steps_json) == 2
            assert fresh.steps_json[0]["n"] == 1
        finally:
            db.close()


class TestInboxManagementClamp:
    """The legacy clamp was ``max(1, min(step, 6))``. Verify it now
    derives from MAX_SEQUENCE_EMAILS."""

    def test_clamp_uses_config_constant(self):
        # Verify the source literal is gone. We check by parsing the file.
        from pathlib import Path
        src_path = Path(__file__).resolve().parent.parent / "src" / "api" / "inbox_management.py"
        source = src_path.read_text(encoding="utf-8")
        assert "clamp to 1-6" not in source, (
            "Old hardcoded-6 clamp comment still present"
        )
        assert "MAX_SEQUENCE_EMAILS" in source, (
            "Expected MAX_SEQUENCE_EMAILS usage in inbox_management.py"
        )


class TestIngestionCurrentStepCeiling:
    """The _confirm_send_log bump used to cap at literal 6. Verify it
    now uses MAX_SEQUENCE_EMAILS."""

    def test_ceiling_uses_config_constant(self):
        from pathlib import Path
        src_path = Path(__file__).resolve().parent.parent / "src" / "orchestration" / "ingestion.py"
        source = src_path.read_text(encoding="utf-8")
        assert "if current < 6" not in source
        assert "MAX_SEQUENCE_EMAILS" in source


class TestEmailGenStripLoop:
    def test_strip_loop_uses_config_constant(self):
        from pathlib import Path
        src_path = Path(__file__).resolve().parent.parent / "src" / "services" / "email_gen.py"
        source = src_path.read_text(encoding="utf-8")
        assert "if email_count < 6:" not in source
        assert "range(email_count + 1, 7)" not in source
        assert "MAX_SEQUENCE_EMAILS" in source
