"""
Randomized stress test for the conference auto-draft sweep.

Substitutes for property-based testing (no Hypothesis dep) by generating
1000 random lead states with fuzzed anchors, drafted-step sets, reply
states, and sequence shapes - then asserts the high-level invariants:

Invariants
----------
I1. No lead with replied_at set ever has a step past email1 drafted.
I2. No step is ever drafted twice for the same lead across repeated sweeps.
I3. Every step that claims to be drafted in _drafted_steps corresponds to
    a Graph call we actually observed for that lead+subject.
I4. Every due-and-not-drafted step gets drafted in exactly one sweep.
I5. Running the sweep N times is equivalent to running it once per step
    becoming due (idempotence of repeated sweeps).
I6. Sweep never mutates draft_created_at or draft_message_id.
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("CONFERENCE_REMINDER_TIMEZONE", "UTC")

from src.db.models import Base, ConferenceLead, User  # noqa: E402
from src.api.conference import autodraft as ad  # noqa: E402
from src.api.conference.prompts import _DEFAULT_SEQ_STEPS  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def user(db):
    u = User(id="u", email="r@x.com", name="R", password_hash="x")
    db.add(u); db.commit()
    return u


class RecordingDraft:
    def __init__(self):
        self.calls = []  # list of {to, subject}
    def create(self, to_email, to_name, subject, body, token_file=None, cc=None, signature_html=""):
        self.calls.append({"to": to_email, "subject": subject})
        return {"message_id": f"mid-{len(self.calls)}"}


@pytest.fixture
def recording_graph(tmp_path):
    rec = RecordingDraft()
    tok = tmp_path / "t.json"; tok.write_text("{}")
    with patch("src.api.conference.service._outlook_token_file", return_value=tok), \
         patch("src.api.conference.service.draft_svc.create_draft_outlook",
               side_effect=rec.create), \
         patch("src.api.conference.service.cancel_followup_reminders", return_value=None), \
         patch("src.api.conference.autodraft.cancel_followup_reminders", return_value=None):
        yield rec


def _random_sequence(rng) -> dict:
    """Return a sequence dict with between 2 and 6 email steps."""
    n = rng.randint(2, 6)
    seq = {}
    for i in range(1, n + 1):
        seq[f"email{i}"] = {"subject": f"subj-{i}", "body": f"body-{i}"}
    return seq


def _random_lead(db, user, rng, lead_id: str, base_now: datetime) -> dict:
    """Insert a random lead. Returns metadata describing its intended state."""
    # Anchor between 0 and 40 days before base_now
    days_before = rng.randint(0, 40)
    hours_offset = rng.randint(0, 23)
    anchor = base_now - timedelta(days=days_before, hours=hours_offset)

    # Maybe already replied (20% chance)
    replied = None
    if rng.random() < 0.20:
        replied = anchor + timedelta(days=rng.randint(0, days_before) if days_before else 0)

    seq = _random_sequence(rng)

    # Pick a random subset of drafted steps (always includes email1)
    drafted = ["email1"]
    for k in list(seq.keys())[1:]:
        if rng.random() < 0.2:
            drafted.append(k)
    seq["_drafted_steps"] = drafted

    lead = ConferenceLead(
        id=lead_id,
        conference_name="C", first_name="F", last_name="L",
        email=f"{lead_id}@x.com",
        user_id=user.id,
        generated_sequence=json.dumps(seq),
        draft_message_id="mid-1",
        draft_created_at=anchor,
        replied_at=replied,
    )
    db.add(lead); db.commit()
    return {
        "id": lead_id,
        "anchor": anchor,
        "replied": replied is not None,
        "seq_keys": set(seq.keys()) - {"_drafted_steps"},
        "drafted": set(drafted),
    }


def _expected_steps_to_draft(meta: dict, now: datetime) -> set:
    """Compute the expected set of step keys that should be drafted for
    this lead given its state and now. Mirrors the sweep's own logic."""
    if meta["replied"]:
        return set()
    expected = set()
    for step_num, day_offset, _, _, *_ in _DEFAULT_SEQ_STEPS:
        if step_num <= 1:
            continue
        step_key = f"email{step_num}"
        if step_key in meta["drafted"]:
            continue
        if step_key not in meta["seq_keys"]:
            continue
        if not ad._step_is_due(meta["anchor"], day_offset, now, timezone.utc):
            continue
        expected.add(step_key)
    return expected


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 42, 1337, 9999])
def test_randomized_stress_invariants(db, user, recording_graph, seed):
    rng = random.Random(seed)
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    N = 60
    metas = [
        _random_lead(db, user, rng, f"L{i}", now)
        for i in range(N)
    ]

    # Sanity: record the initial draft_created_at + draft_message_id for I6
    initial_rows = {
        m["id"]: db.query(ConferenceLead).filter_by(id=m["id"]).first()
        for m in metas
    }
    initial_timestamps = {
        k: (v.draft_created_at, v.draft_message_id)
        for k, v in initial_rows.items()
    }

    # Compute expected drafts per lead
    expected_per_lead = {m["id"]: _expected_steps_to_draft(m, now) for m in metas}
    total_expected = sum(len(v) for v in expected_per_lead.values())

    # Run the sweep twice to verify I5 (repeated sweep idempotent)
    s1 = ad.run_conference_autodraft_sweep(db, user, now_utc=now)
    calls_after_first = len(recording_graph.calls)
    s2 = ad.run_conference_autodraft_sweep(db, user, now_utc=now)

    # I4: first sweep drafts exactly the expected number
    assert s1.steps_drafted == total_expected, (
        f"seed={seed}: first sweep drafted {s1.steps_drafted}, "
        f"expected {total_expected}"
    )

    # I5: second sweep is a no-op
    assert s2.steps_drafted == 0, (
        f"seed={seed}: second sweep drafted {s2.steps_drafted}, should be 0"
    )
    assert len(recording_graph.calls) == calls_after_first, (
        f"seed={seed}: second sweep made extra Graph calls"
    )

    # Per-lead verification of invariants
    for m in metas:
        fresh = db.query(ConferenceLead).filter_by(id=m["id"]).first()
        fresh_seq = json.loads(fresh.generated_sequence)
        fresh_drafted = set(fresh_seq.get("_drafted_steps", []))

        # I1: replied leads are untouched past email1
        if m["replied"]:
            past_email1 = fresh_drafted - {"email1"}
            # only previously-drafted steps may remain; no new ones
            assert past_email1 == (m["drafted"] - {"email1"}), (
                f"seed={seed} lead={m['id']}: replied lead gained new drafts: "
                f"was {m['drafted']}, now {fresh_drafted}"
            )
            continue

        # I2: no duplicates (set-ification in write path)
        assert len(fresh_drafted) == len(set(fresh_drafted))

        # I3: every newly-drafted step corresponds to an observed Graph call
        new_drafts = fresh_drafted - m["drafted"]
        for step_key in new_drafts:
            step_num = int(step_key.replace("email", ""))
            expected_subject = f"subj-{step_num}"
            matching_calls = [
                c for c in recording_graph.calls
                if c["to"] == f"{m['id']}@x.com" and c["subject"] == expected_subject
            ]
            assert len(matching_calls) == 1, (
                f"seed={seed} lead={m['id']} step={step_key}: expected exactly "
                f"1 Graph call, found {len(matching_calls)}"
            )

        # I4: the new drafts match what we expected
        assert new_drafts == expected_per_lead[m["id"]], (
            f"seed={seed} lead={m['id']}: expected {expected_per_lead[m['id']]}, "
            f"got new drafts {new_drafts}"
        )

        # I6: draft_created_at and draft_message_id unchanged
        orig_ts, orig_mid = initial_timestamps[m["id"]]
        assert fresh.draft_created_at == orig_ts, (
            f"seed={seed} lead={m['id']}: draft_created_at changed "
            f"from {orig_ts} to {fresh.draft_created_at}"
        )
        assert fresh.draft_message_id == orig_mid, (
            f"seed={seed} lead={m['id']}: draft_message_id changed"
        )
