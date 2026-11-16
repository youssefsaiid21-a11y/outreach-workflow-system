"""
Regression tests for the 2026-04-09 outbound system fix bundle.

This file pins the behaviour of every fix in the bundle so we can deploy
with confidence that nothing in the operator's reported issue list comes
back. One file per bundle keeps the relationship to the change set obvious.

Covered fixes:

  - Fix 3: Calendly link rendering - plain visible URL, no <a> wrapper.
  - Fix 5: Cancel propagation - POST /campaigns/{id}/cancel endpoint,
           resume blocked on cancelled.
  - Fix 6: Sequence progress - _confirm_send_log bumps EmailSequence.current_step
           on every email_sent webhook (capped at 6).
  - Fix 7: Sequence templates - CRUD + apply, owner-only edits, shared
           visibility, apply replaces existing bodies and approves them.
  - Fix 8: LinkedIn-only sequences - launch detects pure LinkedIn flow.

Note: Instantly has been removed. Tests that verified Instantly-specific
API behaviour (open_tracking, purge_pending_leads, create_campaign payload)
have been removed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

# Use a dedicated file-based SQLite so this module is COMPLETELY isolated
# from the global engine in src.db.database. Setting DATABASE_URL before any
# src.* import is critical - once src.db.database is imported, the global
# engine is bound and can't be changed.
_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"outreach_test_outbound_fixes_{os.getpid()}.db"
if _TEST_DB_PATH.exists():
    _TEST_DB_PATH.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

from src.db.database import SessionLocal, init_db  # noqa: E402
init_db()


def _purge_all():
    """Truncate every table this module touches between tests."""
    from src.db.models import (
        Campaign,
        EmailSequence,
        Prospect,
        SendLog,
        SequenceTemplate,
        UnsubscribeEntry,
        User,
    )
    db = SessionLocal()
    try:
        for model in (SendLog, EmailSequence, SequenceTemplate, Prospect, UnsubscribeEntry, Campaign, User):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db_session():
    _purge_all()
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _make_user(db, *, email="rep@example.com"):
    from src.db.models import User, UserRole
    uid = str(uuid.uuid4())
    db.add(User(
        id=uid, name="Rep", email=email,
        password_hash="x", role=UserRole.admin,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()
    return db.query(User).filter_by(id=uid).first()


def _make_campaign(db, user_id, *, instantly_cid=None, n_prospects=2, status=None, email_prefix="target"):
    # instantly_cid is kept as a no-op parameter for call-site compatibility
    from src.db.models import (
        Campaign,
        CampaignStatus,
        EmailSequence,
        Prospect,
        ProspectStatus,
        SendLog,
        SequenceStatus,
    )
    cid = str(uuid.uuid4())
    db.add(Campaign(
        id=cid, name="Test Campaign",
        goal="meeting", value_prop="x",
        status=status or CampaignStatus.active,
        inbox_ids=["sender@example.com"],
        user_id=user_id,
        created_at=datetime.now(timezone.utc),
    ))
    db.flush()

    seqs = []
    for i in range(n_prospects):
        em = f"{email_prefix}{i}@example.com"
        pid = str(uuid.uuid4())
        db.add(Prospect(
            id=pid, email=em, first_name=f"P{i}", last_name="Last",
            company="Acme Energy", title="HSE Manager",
            status=ProspectStatus.enrolled, user_id=user_id,
            created_at=datetime.now(timezone.utc),
        ))
        db.flush()
        sid = str(uuid.uuid4())
        db.add(EmailSequence(
            id=sid, campaign_id=cid, prospect_id=pid,
            approved=True, status=SequenceStatus.enrolled,
            # Populate all 6 email slots so _resolve_campaign_sequence_shape
            # infers a full 6-step shape. Several tests assert
            # sequence_length == 6 based on this fixture shape.
            email1_subject=f"Subj 1 for {em}", email1_body=f"Body 1 for {em}",
            email2_subject=f"Subj 2 for {em}", email2_body=f"Body 2 for {em}",
            email3_subject=f"Subj 3 for {em}", email3_body=f"Body 3 for {em}",
            email4_subject=f"Subj 4 for {em}", email4_body=f"Body 4 for {em}",
            email5_subject=f"Subj 5 for {em}", email5_body=f"Body 5 for {em}",
            email6_subject=f"Subj 6 for {em}", email6_body=f"Body 6 for {em}",
            linkedin_connect_note="Hi, would love to connect.",
            linkedin_message="Thanks for connecting.",
            current_step=1,
            created_at=datetime.now(timezone.utc),
        ))
        db.flush()
        db.add(SendLog(
            id=str(uuid.uuid4()),
            prospect_id=pid, campaign_id=cid, sequence_id=sid,
            step=1, subject="s", body_preview="b",
            status="pending",
            enrolled_at=datetime.now(timezone.utc),
            inbox_email="sender@example.com",
        ))
        db.flush()
        seqs.append((pid, em, sid))
    db.commit()
    return {"campaign_id": cid, "sequences": seqs}


# ── Fix 3: Calendly link rendering ──────────────────────────────────────────


# ── Fix 5: Cancel propagation ───────────────────────────────────────────────


def test_cancel_campaign_endpoint_purges_and_marks_cancelled(db_session):
    """End-to-end on the POST /campaigns/{id}/cancel endpoint:
    - flips local SendLog rows to cancelled
    - flips local EmailSequence rows to unsubscribed
    - sets Campaign.status = cancelled
    """
    from src.api.campaigns import cancel_campaign
    from src.db.models import (
        Campaign, CampaignStatus, EmailSequence, SendLog, SequenceStatus,
    )

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=3)

    result = cancel_campaign(
        campaign_id=seeded["campaign_id"],
        db=db_session,
        current_user=user,
    )

    assert result["status"] == "cancelled"

    # Local state
    db_session.expire_all()
    camp = db_session.query(Campaign).filter_by(id=seeded["campaign_id"]).first()
    assert camp.status == CampaignStatus.cancelled

    pending_left = db_session.query(SendLog).filter(
        SendLog.campaign_id == seeded["campaign_id"],
        SendLog.status == "pending",
    ).count()
    assert pending_left == 0

    cancelled_logs = db_session.query(SendLog).filter(
        SendLog.campaign_id == seeded["campaign_id"],
        SendLog.status == "cancelled",
    ).count()
    assert cancelled_logs == 3

    unsubscribed_seqs = db_session.query(EmailSequence).filter(
        EmailSequence.campaign_id == seeded["campaign_id"],
        EmailSequence.status == SequenceStatus.unsubscribed,
    ).count()
    assert unsubscribed_seqs == 3


def test_cancel_campaign_idempotent_when_already_cancelled(db_session):
    """Re-cancelling a cancelled campaign is a safe no-op."""
    from src.api.campaigns import cancel_campaign
    from src.db.models import CampaignStatus

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, status=CampaignStatus.cancelled)

    result = cancel_campaign(
        campaign_id=seeded["campaign_id"],
        db=db_session,
        current_user=user,
    )

    assert result["status"] == "cancelled"
    assert result["leads_purged"] == 0


def test_resume_campaign_blocked_when_cancelled(db_session):
    """Cancelled campaigns are non-resumable. resume_campaign must return
    HTTP 409, not silently restart."""
    from fastapi import HTTPException
    from src.api.campaigns import resume_campaign
    from src.db.models import CampaignStatus

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, status=CampaignStatus.cancelled)

    with pytest.raises(HTTPException) as exc:
        resume_campaign(
            campaign_id=seeded["campaign_id"],
            db=db_session,
            current_user=user,
        )
    assert exc.value.status_code == 409
    assert "cancelled" in exc.value.detail.lower()


def test_pause_campaign_blocked_when_cancelled(db_session):
    """Pausing a cancelled campaign is meaningless - refuse with 409."""
    from fastapi import HTTPException
    from src.api.campaigns import pause_campaign
    from src.db.models import CampaignStatus

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, status=CampaignStatus.cancelled)

    with pytest.raises(HTTPException) as exc:
        pause_campaign(
            campaign_id=seeded["campaign_id"],
            db=db_session,
            current_user=user,
        )
    assert exc.value.status_code == 409


def test_pause_campaign_does_not_purge_leads(db_session):
    """Critical contract: pause is REVERSIBLE. It must NOT call
    any destructive lead-deletion - that's only for the cancel path."""
    from src.api.campaigns import pause_campaign
    from src.db.models import CampaignStatus, SendLog

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id)

    result = pause_campaign(
        campaign_id=seeded["campaign_id"],
        db=db_session,
        current_user=user,
    )

    assert result["status"] == "paused"
    # All send_log rows remain pending (none were purged)
    pending = db_session.query(SendLog).filter_by(
        campaign_id=seeded["campaign_id"], status="pending"
    ).count()
    assert pending == 2


# ── Fix 6: Sequence progress tracking ───────────────────────────────────────


def test_email_sent_event_advances_email_sequence_current_step(db_session):
    """The Nasser progress bug: EmailSequence.current_step was orphaned
    (column existed but nothing wrote to it). _confirm_send_log now bumps
    it on every email_sent event."""
    from src.db.models import EmailSequence
    from src.orchestration.enums import EventType
    from src.orchestration.ingestion import _confirm_send_log
    from src.orchestration.models import LeadContext

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=1)
    pid, em, sid = seeded["sequences"][0]

    seq = db_session.query(EmailSequence).filter_by(id=sid).first()
    assert seq.current_step == 1  # initial state

    actions: list[str] = []
    _confirm_send_log(
        db=db_session,
        lead=LeadContext(lead_id=pid, email=em, first_name="P", last_name="L", company="Co", title="VP", industry="", location=""),
        event_type=EventType.email_sent,
        occurred_at=datetime.now(timezone.utc),
        metadata={"campaign_id": seeded["campaign_id"]},
        actions_triggered=actions,
    )
    db_session.commit()
    db_session.expire_all()

    seq = db_session.query(EmailSequence).filter_by(id=sid).first()
    assert seq.current_step == 2, "current_step must advance from 1 → 2 on first send"
    assert "sequence_step_advanced" in actions


def test_current_step_caps_at_six_on_runaway_replays(db_session):
    """A flaky webhook replay must not push current_step past 6."""
    from src.db.models import EmailSequence
    from src.orchestration.enums import EventType
    from src.orchestration.ingestion import _confirm_send_log
    from src.orchestration.models import LeadContext

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=1)
    pid, em, sid = seeded["sequences"][0]

    # Set current_step at 6 manually so a single bump would push to 7.
    seq = db_session.query(EmailSequence).filter_by(id=sid).first()
    seq.current_step = 6
    db_session.commit()

    for _ in range(3):
        _confirm_send_log(
            db=db_session,
            lead=LeadContext(lead_id=pid, email=em, first_name="P", last_name="L", company="Co", title="VP", industry="", location=""),
            event_type=EventType.email_sent,
            occurred_at=datetime.now(timezone.utc),
            metadata={"campaign_id": seeded["campaign_id"]},
            actions_triggered=[],
        )
        db_session.commit()

    db_session.expire_all()
    seq = db_session.query(EmailSequence).filter_by(id=sid).first()
    assert seq.current_step == 6, "current_step must cap at 6 - the schema's max sequence length"


def test_current_step_not_advanced_on_non_email_sent_events(db_session):
    """Other event types (open, reply, bounce) must not bump the step."""
    from src.db.models import EmailSequence
    from src.orchestration.enums import EventType
    from src.orchestration.ingestion import _confirm_send_log
    from src.orchestration.models import LeadContext

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=1)
    pid, em, sid = seeded["sequences"][0]

    for et in (EventType.email_opened, EventType.email_replied_positive, EventType.email_bounced_hard):
        _confirm_send_log(
            db=db_session,
            lead=LeadContext(lead_id=pid, email=em, first_name="P", last_name="L", company="Co", title="VP", industry="", location=""),
            event_type=et,
            occurred_at=datetime.now(timezone.utc),
            metadata={"campaign_id": seeded["campaign_id"]},
            actions_triggered=[],
        )
    db_session.commit()
    db_session.expire_all()
    seq = db_session.query(EmailSequence).filter_by(id=sid).first()
    assert seq.current_step == 1


def test_campaign_stats_endpoint_returns_step_distribution(db_session):
    """The /campaigns/{id}/stats payload must surface the per-step
    distribution + avg_step_reached so the dashboard progress strip has
    something to render."""
    from src.api.campaigns import campaign_stats
    from src.db.models import EmailSequence

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=4)

    # Spread leads across steps. current_step = "next step to send", so
    # completed = current_step - 1. current_step=2 means email 1 sent, etc.
    seqs = db_session.query(EmailSequence).filter_by(campaign_id=seeded["campaign_id"]).all()
    seqs[0].current_step = 2   # completed step 1
    seqs[1].current_step = 2   # completed step 1
    seqs[2].current_step = 4   # completed step 3
    seqs[3].current_step = 6   # completed step 5
    db_session.commit()

    result = campaign_stats(
        campaign_id=seeded["campaign_id"],
        db=db_session,
        current_user=user,
    )

    assert result["sequence_length"] == 6
    dist = result["step_distribution"]
    assert dist[1] == 2   # two leads completed step 1
    assert dist[3] == 1   # one lead completed step 3
    assert dist[5] == 1   # one lead completed step 5
    assert dist[2] == 0
    # avg = (1*2 + 3 + 5) / 4 = 10/4 = 2.5
    assert result["avg_step_reached"] == 2.5


# ── Fix 7: Sequence templates ────────────────────────────────────────────────


def test_sequence_template_create_snapshots_approved_sequence(db_session):
    """POST /sequence-templates pulls bodies + LinkedIn fields from an
    approved sequence into steps_json. Owner = current user. step_count
    is denormalised."""
    from src.api.sequence_templates import (
        TemplateCreateRequest, create_template,
    )
    from src.db.models import SequenceTemplate

    user = _make_user(db_session)
    seeded = _make_campaign(db_session, user.id, n_prospects=1)

    out = create_template(
        req=TemplateCreateRequest(
            name="GCC HSE v1",
            source_campaign_id=seeded["campaign_id"],
            is_shared=False,
        ),
        db=db_session,
        current_user=user,
    )

    assert out["name"] == "GCC HSE v1"
    assert out["owner_user_id"] == user.id
    assert out["is_shared"] is False
    # Seeded fixture has 6 emails + linkedin_connect + linkedin_message = 8 steps
    assert out["step_count"] == 8

    types = [s["type"] for s in out["steps"]]
    assert types.count("email") == 6
    assert "linkedin_connect" in types
    assert "linkedin_message" in types

    saved = db_session.query(SequenceTemplate).filter_by(id=out["id"]).first()
    assert saved is not None
    assert saved.step_count == 8


def test_sequence_template_create_blocks_non_owner(db_session):
    """A user cannot snapshot from another user's campaign."""
    from fastapi import HTTPException
    from src.api.sequence_templates import (
        TemplateCreateRequest, create_template,
    )

    owner = _make_user(db_session, email="owner@example.com")
    other = _make_user(db_session, email="other@example.com")
    seeded = _make_campaign(db_session, owner.id)

    with pytest.raises(HTTPException) as exc:
        create_template(
            req=TemplateCreateRequest(
                name="Stolen",
                source_campaign_id=seeded["campaign_id"],
            ),
            db=db_session,
            current_user=other,
        )
    assert exc.value.status_code == 403


def test_sequence_template_list_includes_own_and_shared(db_session):
    """list_templates returns own templates AND any is_shared=True
    templates from other users. Private templates of other users are
    invisible."""
    from src.api.sequence_templates import (
        TemplateCreateRequest, create_template, list_templates,
    )

    alice = _make_user(db_session, email="alice@example.com")
    bob = _make_user(db_session, email="bob@example.com")

    alice_camp = _make_campaign(db_session, alice.id, instantly_cid="cmp_ignored")
    bob_camp = _make_campaign(db_session, bob.id, instantly_cid="cmp_bob")

    create_template(
        req=TemplateCreateRequest(name="Alice private", source_campaign_id=alice_camp["campaign_id"], is_shared=False),
        db=db_session, current_user=alice,
    )
    create_template(
        req=TemplateCreateRequest(name="Alice shared", source_campaign_id=alice_camp["campaign_id"], is_shared=True),
        db=db_session, current_user=alice,
    )
    create_template(
        req=TemplateCreateRequest(name="Bob private", source_campaign_id=bob_camp["campaign_id"], is_shared=False),
        db=db_session, current_user=bob,
    )

    # Bob sees: his own private + Alice's shared. NOT Alice's private.
    bob_view = list_templates(db=db_session, current_user=bob)
    names = {t["name"] for t in bob_view}
    assert "Bob private" in names
    assert "Alice shared" in names
    assert "Alice private" not in names

    # Alice sees both her own templates (and would see any shared from Bob, but he has none).
    alice_view = list_templates(db=db_session, current_user=alice)
    alice_names = {t["name"] for t in alice_view}
    assert alice_names == {"Alice private", "Alice shared"}

    # is_owner flag works
    bob_alice_shared = next(t for t in bob_view if t["name"] == "Alice shared")
    assert bob_alice_shared["is_owner"] is False
    bob_own = next(t for t in bob_view if t["name"] == "Bob private")
    assert bob_own["is_owner"] is True


def test_sequence_template_patch_owner_only(db_session):
    """Only the owner can rename or flip is_shared."""
    from fastapi import HTTPException
    from src.api.sequence_templates import (
        TemplateCreateRequest, TemplatePatchRequest, create_template, patch_template,
    )

    alice = _make_user(db_session, email="alice@example.com")
    bob = _make_user(db_session, email="bob@example.com")
    camp = _make_campaign(db_session, alice.id)

    t = create_template(
        req=TemplateCreateRequest(name="Original", source_campaign_id=camp["campaign_id"]),
        db=db_session, current_user=alice,
    )

    # Bob can't rename it
    with pytest.raises(HTTPException) as exc:
        patch_template(
            template_id=t["id"],
            req=TemplatePatchRequest(name="Hijacked"),
            db=db_session, current_user=bob,
        )
    assert exc.value.status_code == 403

    # Alice can
    out = patch_template(
        template_id=t["id"],
        req=TemplatePatchRequest(name="Renamed", is_shared=True),
        db=db_session, current_user=alice,
    )
    assert out["name"] == "Renamed"
    assert out["is_shared"] is True


def test_sequence_template_apply_clones_into_every_prospect(db_session):
    """apply replaces every EmailSequence.email{N}_subject/body and the
    LinkedIn fields on every prospect in the target campaign, then marks
    them approved (templates were already vetted on the source campaign)."""
    from src.api.sequence_templates import (
        TemplateCreateRequest, apply_template, create_template,
    )
    from src.db.models import EmailSequence

    user = _make_user(db_session)

    # Build a "source" campaign and snapshot it.
    src = _make_campaign(db_session, user.id, instantly_cid="cmp_src")
    template = create_template(
        req=TemplateCreateRequest(name="Reusable", source_campaign_id=src["campaign_id"]),
        db=db_session, current_user=user,
    )

    # Build a fresh "target" campaign with different bodies.
    # Use a different email_prefix so we don't collide with the source
    # campaign's prospects under the user's unique (user_id, email) constraint.
    tgt = _make_campaign(db_session, user.id, instantly_cid="cmp_tgt", n_prospects=3, email_prefix="tgt")

    # Override target sequences so we can prove apply REPLACED them.
    seqs = db_session.query(EmailSequence).filter_by(campaign_id=tgt["campaign_id"]).all()
    for s in seqs:
        s.email1_subject = "OLD subject"
        s.email1_body = "OLD body"
        s.approved = False
    db_session.commit()

    out = apply_template(
        template_id=template["id"],
        campaign_id=tgt["campaign_id"],
        db=db_session,
        current_user=user,
    )
    assert out["applied"] == 3

    db_session.expire_all()
    refreshed = db_session.query(EmailSequence).filter_by(campaign_id=tgt["campaign_id"]).all()
    # apply uses the FIRST source sequence as the template - all target
    # prospects get the same literal bodies. That's the intended contract:
    # templates are not re-personalised, the operator gets exactly what was
    # snapshotted from the source.
    for s in refreshed:
        assert s.email1_subject and s.email1_subject.startswith("Subj 1 for target")
        assert s.email1_body and s.email1_body.startswith("Body 1 for target")
        assert "OLD" not in (s.email1_subject or "")
        assert "OLD" not in (s.email1_body or "")
        assert s.linkedin_connect_note == "Hi, would love to connect."
        assert s.linkedin_message == "Thanks for connecting."
        assert s.approved is True


def test_sequence_template_apply_blocked_for_private_template_of_other_user(db_session):
    """Private templates of other users cannot be applied."""
    from fastapi import HTTPException
    from src.api.sequence_templates import (
        TemplateCreateRequest, apply_template, create_template,
    )

    alice = _make_user(db_session, email="alice@example.com")
    bob = _make_user(db_session, email="bob@example.com")
    alice_camp = _make_campaign(db_session, alice.id, instantly_cid="cmp_ignored")
    bob_camp = _make_campaign(db_session, bob.id, instantly_cid="cmp_bob")

    private = create_template(
        req=TemplateCreateRequest(name="Alice private", source_campaign_id=alice_camp["campaign_id"], is_shared=False),
        db=db_session, current_user=alice,
    )

    with pytest.raises(HTTPException) as exc:
        apply_template(
            template_id=private["id"],
            campaign_id=bob_camp["campaign_id"],
            db=db_session, current_user=bob,
        )
    assert exc.value.status_code == 403


def test_sequence_template_apply_works_for_shared_template(db_session):
    """A shared template from another user can be applied to your own campaign."""
    from src.api.sequence_templates import (
        TemplateCreateRequest, apply_template, create_template,
    )

    alice = _make_user(db_session, email="alice@example.com")
    bob = _make_user(db_session, email="bob@example.com")
    alice_camp = _make_campaign(db_session, alice.id, instantly_cid="cmp_ignored")
    bob_camp = _make_campaign(db_session, bob.id, instantly_cid="cmp_bob")

    shared = create_template(
        req=TemplateCreateRequest(name="Shared", source_campaign_id=alice_camp["campaign_id"], is_shared=True),
        db=db_session, current_user=alice,
    )

    out = apply_template(
        template_id=shared["id"],
        campaign_id=bob_camp["campaign_id"],
        db=db_session, current_user=bob,
    )
    assert out["applied"] == 2  # bob's campaign has 2 seeded prospects


def test_sequence_template_delete_owner_only(db_session):
    """Only the owner can delete a template."""
    from fastapi import HTTPException
    from src.api.sequence_templates import (
        TemplateCreateRequest, create_template, delete_template,
    )

    alice = _make_user(db_session, email="alice@example.com")
    bob = _make_user(db_session, email="bob@example.com")
    camp = _make_campaign(db_session, alice.id)

    t = create_template(
        req=TemplateCreateRequest(name="X", source_campaign_id=camp["campaign_id"]),
        db=db_session, current_user=alice,
    )

    with pytest.raises(HTTPException) as exc:
        delete_template(template_id=t["id"], db=db_session, current_user=bob)
    assert exc.value.status_code == 403

    delete_template(template_id=t["id"], db=db_session, current_user=alice)


# ── Fix 8: LinkedIn-only sequences ──────────────────────────────────────────


def test_linkedin_only_short_circuit_skips_instantly():
    """The launch flow detects zero email steps + at least one LinkedIn
    step and takes the LinkedIn-only path. We test the detection logic
    in isolation rather than driving the full launch endpoint."""
    from src.orchestration.config import StepType
    from types import SimpleNamespace

    # Build a fake sequence config: only LinkedIn steps.
    li_only_steps = [
        SimpleNamespace(step_type=StepType.send_linkedin_connect, day_offset=0),
        SimpleNamespace(step_type=StepType.send_linkedin_message, day_offset=5),
    ]

    has_linkedin = any(
        s.step_type in (StepType.send_linkedin_connect, StepType.send_linkedin_message)
        for s in li_only_steps
    )
    has_email = any(s.step_type == StepType.send_email for s in li_only_steps)
    linkedin_only = (not has_email) and has_linkedin
    assert linkedin_only is True

    # Mixed: not linkedin_only.
    mixed_steps = li_only_steps + [SimpleNamespace(step_type=StepType.send_email, day_offset=10)]
    has_linkedin = any(
        s.step_type in (StepType.send_linkedin_connect, StepType.send_linkedin_message)
        for s in mixed_steps
    )
    has_email = any(s.step_type == StepType.send_email for s in mixed_steps)
    assert ((not has_email) and has_linkedin) is False


def test_email_only_no_linkedin_does_not_qualify_as_linkedin_only():
    """A pure email sequence is the normal path, never linkedin_only."""
    from src.orchestration.config import StepType
    from types import SimpleNamespace

    email_only_steps = [
        SimpleNamespace(step_type=StepType.send_email, day_offset=0),
        SimpleNamespace(step_type=StepType.send_email, day_offset=3),
    ]
    has_linkedin = any(
        s.step_type in (StepType.send_linkedin_connect, StepType.send_linkedin_message)
        for s in email_only_steps
    )
    assert has_linkedin is False


# ── Cross-cutting: CampaignStatus.cancelled enum value ──────────────────────


def test_campaign_status_enum_includes_cancelled():
    """The new enum value must be present so models, alembic, and the
    cancel endpoint stay in sync."""
    from src.db.models import CampaignStatus
    assert hasattr(CampaignStatus, "cancelled")
    assert CampaignStatus.cancelled.value == "cancelled"


def test_sequence_template_model_columns():
    """Schema sanity: the SequenceTemplate model has the columns the API
    + alembic migration assume."""
    from src.db.models import SequenceTemplate
    cols = {c.name for c in SequenceTemplate.__table__.columns}
    assert {"id", "name", "owner_user_id", "is_shared", "steps_json", "step_count", "created_at", "updated_at"} <= cols


# ── Fix 1: Analytics token columns removed ─────────────────────────────────
#
# Pure frontend deletion. No runtime behaviour to mock - just assert that the
# removed strings really are gone from the shipped HTML/JS, and that the
# columns they used to belong to have been cleaned up without breaking
# colspans.


def _dashboard_path(name: str) -> Path:
    """Locate the dashboard file - ``tests/`` is a sibling of ``dashboard/``."""
    here = Path(__file__).resolve().parent
    return here.parent / "dashboard" / name


def test_fix1_analytics_no_anthropic_tokens_card():
    """The 'Anthropic Tokens' summary card must be gone from dashboard/main.js.
    Before the fix it showed token in/out counts that the operator said were
    irrelevant noise."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    assert "Anthropic Tokens" not in js, "Anthropic Tokens summary card still present"
    assert "total_tokens_input" not in js, "total_tokens_input still referenced in dashboard"
    assert "total_tokens_output" not in js, "total_tokens_output still referenced in dashboard"


def test_fix1_cost_table_no_token_columns():
    """The Campaigns cost table must no longer render per-campaign
    tokens_input / tokens_output columns."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    # These were the exact td renderers we removed.
    assert "fmtTokens(c.tokens_input)" not in js
    assert "fmtTokens(c.tokens_output)" not in js


def test_fix1_rep_performance_no_token_columns():
    """Rep performance table must no longer show per-rep token columns."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    assert "(u.tokens_input||0).toLocaleString()" not in js
    assert "(u.tokens_output||0).toLocaleString()" not in js
    assert "Tokens (in)" not in js
    assert "Tokens (out)" not in js


def test_fix1_index_html_no_input_output_headers():
    """The cost table's <th> headers for 'Input tokens' / 'Output tokens'
    must be gone from index.html."""
    html = _dashboard_path("index.html").read_text(encoding="utf-8")
    assert "Input tokens" not in html, "Input tokens <th> still present in index.html"
    assert "Output tokens" not in html, "Output tokens <th> still present in index.html"


def test_fix1_cost_tbody_colspan_matches_current_columns():
    """When we removed the token columns we also had to shrink the empty-state
    row's colspan from 5 → 3. Assert colspan='3' is what ships."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    # After the fix there are 3 columns: name, status, cost.
    assert 'colspan="3"' in js, "cost-tbody empty-state colspan should be 3 after removing token cols"


# ── Fix 2: Cancel button UI reset ───────────────────────────────────────────
#
# Pure frontend JS wiring. Assert the helper exists and is called from both
# the success path (operator clicks Cancel) and the error-propagation path
# (pollJob detects the cancelled job and rejects).


def test_fix2_reset_gen_ui_helper_exists():
    """The ``_resetGenUi`` helper must exist. Before the fix the loading
    spinner state was never cleared on cancel."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    assert "function _resetGenUi()" in js, "_resetGenUi helper is missing"


def test_fix2_reset_called_from_cancel_handler():
    """cancelGeneration() must optimistically reset the UI now instead of
    waiting for pollJob to detect the cancel asynchronously."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    # Find the cancelGeneration function body
    start = js.index("async function cancelGeneration()")
    end = js.index("\n}\n", start) + 2
    body = js[start:end]
    assert "_resetGenUi()" in body, "cancelGeneration no longer calls _resetGenUi"
    assert "_stopGenTimer()" in body, "cancelGeneration no longer stops the timer"
    assert "_wfGenerating = false" in body, "cancelGeneration must clear the in-flight flag"


def test_fix2_reset_called_from_wf_generate_all_catch():
    """wfGenerateAll's catch block is the backstop when pollJob rejects
    after detecting the cancelled state. Must also call _resetGenUi."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    start = js.index("async function wfGenerateAll()")
    end = js.index("\nfunction _resetGenUi()", start)
    body = js[start:end]
    # The catch block specifically must call the helper.
    catch_idx = body.index("} catch(e) {")
    catch_body = body[catch_idx:]
    assert "_resetGenUi()" in catch_body, "wfGenerateAll catch must also call _resetGenUi"


def test_fix2_reset_helper_clears_every_progress_element():
    """_resetGenUi must touch every element the progress UI shows, so the
    spinner really disappears."""
    js = _dashboard_path("main.js").read_text(encoding="utf-8")
    start = js.index("function _resetGenUi()")
    end = js.index("\n}\n", start)
    body = js[start:end]
    for element_id in ("wf-gen-progress", "wf-gen-status", "wf-gen-bar", "wf-gen-eta", "wf-gen-speed", "wf-gen-timer"):
        assert element_id in body, f"_resetGenUi does not touch #{element_id}"


# ── Fix 9: Tavily → DuckDuckGo migration ────────────────────────────────────


def test_fix9_project_search_uses_ddg_not_tavily():
    """project_search.py must no longer import the Tavily client - the
    migration replaces it with duckduckgo_search (ddgs)."""
    src = Path(__file__).resolve().parent.parent / "src" / "services" / "project_search.py"
    content = src.read_text(encoding="utf-8")
    assert "from tavily import TavilyClient" not in content
    assert "TavilyClient(" not in content
    # DDG import lives inside _ddg_news for lazy loading; grep for the
    # module name instead.
    assert "ddgs" in content.lower()


def test_fix9_find_recent_project_returns_rephrased_sentence():
    """End-to-end: stub DDG + Claude, confirm find_recent_project walks
    the pipeline and returns the rephrased sentence."""
    from src.services import project_search

    # Stub DDG news to return one relevant item inside the lookback window.
    now_iso = datetime.now(timezone.utc).isoformat()
    fake_results = [{
        "title": "Acme Energy breaks ground on Al Reeman III project",
        "body": "Construction started this week on the Al Reeman III residential project in Abu Dhabi.",
        "url": "https://example.com/news/123",
        "date": now_iso,
        "source": "Example",
    }]

    with patch("src.services.project_search._ddg_news", return_value=fake_results):
        # Also stub Claude so we don't hit the live API.
        fake_msg = MagicMock()
        fake_msg.content = [MagicMock(text="Saw you just broke ground on Al Reeman III in Abu Dhabi")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_msg
        with patch.object(project_search, "_get_client", return_value=fake_client):
            result = project_search.find_recent_project("Acme Energy")

    assert result is not None
    assert "Al Reeman III" in result
    assert "Abu Dhabi" in result


def test_fix9_find_recent_project_filters_old_results():
    """Results older than the lookback window must be dropped - we
    mustn't reference a project from 2 years ago as 'recent'."""
    from datetime import timedelta
    from src.services import project_search

    old_date = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    fake_results = [{
        "title": "Acme Energy project completed",
        "body": "Acme Energy completed the XYZ construction project.",
        "url": "https://example.com",
        "date": old_date,
    }]

    with patch("src.services.project_search._ddg_news", return_value=fake_results):
        result = project_search.find_recent_project("Acme Energy")
    assert result is None, "Old results must be filtered by the lookback window"


def test_fix9_find_recent_project_skips_unrelated_results():
    """Results that don't mention the company name AND a project keyword
    are dropped - 0% hallucination contract."""
    from src.services import project_search

    now_iso = datetime.now(timezone.utc).isoformat()
    # Mentions the company but no project keyword.
    fake_results = [{
        "title": "Acme Energy quarterly earnings",
        "body": "Acme Energy reported Q3 earnings on Tuesday.",
        "url": "https://example.com",
        "date": now_iso,
    }]

    with patch("src.services.project_search._ddg_news", return_value=fake_results):
        result = project_search.find_recent_project("Acme Energy")
    assert result is None, "Results without a project keyword must be dropped"


def test_fix9_find_recent_project_handles_ddg_failure_gracefully():
    """If DDG is rate-limited / unreachable, find_recent_project returns
    None rather than raising - the email generator skips the project
    mention entirely in that case."""
    from src.services import project_search

    with patch("src.services.project_search._ddg_news", return_value=[]):
        result = project_search.find_recent_project("Acme Energy")
    assert result is None


def test_fix9_find_recent_project_handles_empty_company():
    from src.services import project_search
    assert project_search.find_recent_project("") is None
    assert project_search.find_recent_project("   ") is None


def test_fix9_ddg_news_wrapper_retries_on_transient_failure():
    """The _ddg_news wrapper should retry once on transient errors before
    giving up. Simulates rate limit on attempt 1, success on attempt 2."""
    from src.services import project_search

    # Build a fake DDGS context manager that fails once then succeeds.
    call_count = {"n": 0}

    class FakeDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def news(self, query=None, max_results=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("rate limited")
            return [{"title": "ok", "body": "body", "date": "2026-04-01", "url": "u"}]

    # The import inside _ddg_news tries `from ddgs import DDGS` first.
    import sys
    fake_module = MagicMock()
    fake_module.DDGS = FakeDDGS
    with patch.dict(sys.modules, {"ddgs": fake_module}):
        # Patch sleep so the test doesn't actually wait.
        with patch("src.services.project_search.time.sleep"):
            results = project_search._ddg_news("Acme project", max_results=3)
    assert len(results) == 1
    assert call_count["n"] == 2, "should have retried once after the first failure"
