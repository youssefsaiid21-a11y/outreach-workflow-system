"""
Hardening 1.2, 1.4, 2.3, 2.4 - pre-flight + safety checks on launch_campaign.

Pins the operator-visible behaviour added during the post-Nasser-v1.0
hardening pass:

- 1.2: pre-flight asserts on the built sequence (cumulative delay 0..90,
  no consecutive zero-delay duplicate subjects, every step has a non-empty
  subject AND body on the sample sequence).
- 1.4: pre-flight suppression check - refuses 409 if any approved
  sequence is on the suppression list.
- 2.4: per-campaign launch lock - concurrent launch_campaign calls for
  the same campaign id refuse with 409 instead of running twice.

Note: Instantly has been removed. The launch flow is LinkedIn-only via
PhantomBuster. Tests that previously verified Instantly API calls
(stop_on_reply, create_campaign payload) have been removed.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")


def _build_fixture(monkeypatch, *, n_prospects=1, suppress_emails=None, custom_seq_steps=None, broken_subject_n=None):
    """Build an in-memory campaign + N prospects + sequences. Optionally
    add some prospects to the suppression list, or omit subject on email N."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.db.database import Base
    from src.db import models  # noqa: F401
    from src.orchestration import orm as _orch_orm  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()

    from src.db.models import (
        AppSetting,
        Campaign,
        CampaignStatus,
        EmailSequence,
        Prospect,
        SequenceStatus,
        UnsubscribeEntry,
        User,
        UserRole,
    )

    user_id = str(uuid.uuid4())
    db.add(User(
        id=user_id, name="Test", email="rep@example.com",
        password_hash="x", role=UserRole.admin,
        created_at=datetime.now(timezone.utc),
    ))

    campaign_id = str(uuid.uuid4())
    db.add(Campaign(
        id=campaign_id,
        name="Hardening Test",
        goal="meeting", value_prop="x",
        status=CampaignStatus.draft,
        inbox_ids=["sender@example.com"],
        user_id=user_id,
        created_at=datetime.now(timezone.utc),
    ))

    suppress_set = set(suppress_emails or [])

    for i in range(n_prospects):
        em = f"lead{i}@example.com"
        pid = str(uuid.uuid4())
        db.add(Prospect(
            id=pid, email=em, first_name=f"Lead{i}", last_name="One",
            company="Co", title="VP", status="new", user_id=user_id,
            created_at=datetime.now(timezone.utc),
        ))
        # Sequence - by default all 6 emails are present.
        seq_kwargs = {f"email{n}_subject": f"s{n}" for n in range(1, 7)}
        seq_kwargs.update({f"email{n}_body": f"b{n}" for n in range(1, 7)})
        if broken_subject_n is not None:
            seq_kwargs[f"email{broken_subject_n}_subject"] = ""
        db.add(EmailSequence(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id, prospect_id=pid,
            approved=True, status=SequenceStatus.pending,
            created_at=datetime.now(timezone.utc),
            **seq_kwargs,
        ))
        if em in suppress_set:
            db.add(UnsubscribeEntry(email=em, reason="test", source="test", user_id=user_id))

    if custom_seq_steps is not None:
        import json as _json
        db.add(AppSetting(key="custom_sequence_config", value=_json.dumps({
            "sequence_id": "test-seq", "name": "Test", "steps": custom_seq_steps,
        })))
        from src.orchestration import config as _orch_cfg
        _orch_cfg.load_custom_config_from_db()

    db.commit()

    from src.api import campaigns as campaigns_mod
    # Reset the launch lock map between tests so the per-campaign lock
    # doesn't bleed across runs.
    campaigns_mod._LAUNCH_LOCKS.clear()

    return campaign_id, db, user_id


def _step(day: int, idx: int, kind: str = "send_email") -> dict:
    return {
        "step_id": f"step-{idx}", "step_type": kind, "day_offset": day,
        "template_id": None, "conditions": [], "enabled": True,
        "note_template_id": None, "variant_id": None, "label": None,
    }


# ── 1.4 suppression pre-flight ──────────────────────────────────────────


def test_suppression_pre_flight_refuses_409(monkeypatch):
    cid, db, uid = _build_fixture(
        monkeypatch, n_prospects=3, suppress_emails={"lead1@example.com"},
    )

    from fastapi import HTTPException
    from src.api.campaigns import launch_campaign
    from src.db.models import User
    cu = db.query(User).filter_by(id=uid).first()
    with pytest.raises(HTTPException) as exc:
        launch_campaign(campaign_id=cid, db=db, current_user=cu)
    assert exc.value.status_code == 409
    assert "suppression" in str(exc.value.detail).lower()
    assert "lead1@example.com" in str(exc.value.detail)


# ── 1.2 pre-flight asserts on built sequence ────────────────────────────


def test_pre_flight_refuses_missing_subject(monkeypatch):
    # Email 2 has subject="" → 1.2 #3 should refuse.
    cid, db, uid = _build_fixture(monkeypatch, broken_subject_n=2)

    from fastapi import HTTPException
    from src.api.campaigns import launch_campaign
    from src.db.models import User
    cu = db.query(User).filter_by(id=uid).first()
    with pytest.raises(HTTPException) as exc:
        launch_campaign(campaign_id=cid, db=db, current_user=cu)
    assert exc.value.status_code == 422
    assert "email2" in str(exc.value.detail).lower()


def test_pre_flight_refuses_excessive_cumulative_delay(monkeypatch):
    """Custom sequence summing to >90 days must be refused."""
    # Days [0, 50, 200] → cumulative gaps = 50 + 150 + 0 = 200 (>90)
    custom = [_step(0, 1), _step(50, 2), _step(200, 3)]
    cid, db, uid = _build_fixture(monkeypatch, custom_seq_steps=custom)

    from fastapi import HTTPException
    from src.api.campaigns import launch_campaign
    from src.db.models import User
    cu = db.query(User).filter_by(id=uid).first()
    with pytest.raises(HTTPException) as exc:
        launch_campaign(campaign_id=cid, db=db, current_user=cu)
    assert exc.value.status_code == 422
    assert "0..90" in str(exc.value.detail) or "90" in str(exc.value.detail)


# ── 2.4 launch lock ─────────────────────────────────────────────────────


def test_launch_lock_refuses_concurrent(monkeypatch):
    """A second concurrent launch_campaign for the same campaign id
    must get 409 immediately, not queue behind the first."""
    import time

    # Use an event to hold the first launch inside the lock long enough
    # for the second to race against it.
    release = threading.Event()

    cid, db, uid = _build_fixture(monkeypatch)

    # Patch the PhantomBuster / LinkedIn dispatch so the first launch
    # blocks rather than completing immediately.
    from src.api import campaigns as campaigns_mod

    _original_launch = campaigns_mod._do_linkedin_launch if hasattr(campaigns_mod, "_do_linkedin_launch") else None

    def _blocking_launch(*args, **kwargs):
        release.wait(timeout=5)
        if _original_launch:
            return _original_launch(*args, **kwargs)

    if hasattr(campaigns_mod, "_do_linkedin_launch"):
        monkeypatch.setattr(campaigns_mod, "_do_linkedin_launch", _blocking_launch)

    from src.api.campaigns import launch_campaign
    from src.db.models import User
    cu = db.query(User).filter_by(id=uid).first()

    results: list = []

    def _t1():
        try:
            results.append(("t1", launch_campaign(campaign_id=cid, db=db, current_user=cu)))
        except Exception as e:
            results.append(("t1", e))

    t = threading.Thread(target=_t1)
    t.start()

    # Give t1 time to enter the lock
    time.sleep(0.2)

    # Second launch should hit the lock and refuse with 409.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        launch_campaign(campaign_id=cid, db=db, current_user=cu)
    assert exc.value.status_code == 409
    assert "in progress" in str(exc.value.detail).lower()

    # Let t1 finish.
    release.set()
    t.join(timeout=10)
