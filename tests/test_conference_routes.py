"""
P4-5 - integration tests for the ``/conference`` HTTP surface.

These tests mount only the conference sub-package router on a fresh
FastAPI app with a SQLite in-memory session and a stub user. They cover
per-user isolation (the core multi-tenancy guarantee from audit #7) and
the happy path for import → list → generate → draft.

External dependencies (Anthropic, Outlook) are monkeypatched to avoid
network calls.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import ConferenceLead, User, UserRole
from src.api.auth import get_current_user


@pytest.fixture
def engine():
    # StaticPool + single connection so every sessionmaker call reuses the
    # same in-memory SQLite database. Otherwise each new connection sees a
    # fresh empty DB and the test's fixture rows disappear.
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def SessionLocal(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db(SessionLocal):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_user(db, name: str, email: str, role: UserRole = UserRole.rep) -> User:
    u = User(
        id=str(uuid.uuid4()),
        name=name,
        email=email,
        password_hash="unused",
        role=role,
    )
    db.add(u)
    db.commit()
    return u


def _build_app(SessionLocal, current_user_getter):
    """Mount the conference router on a fresh app with overridden deps."""
    from src.api.conference import router as conference_router

    app = FastAPI()
    app.include_router(conference_router, prefix="/api")

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = current_user_getter
    return app


# ── Fixtures: a two-user world with a stub auth switcher ────────────────


@pytest.fixture
def world(db, SessionLocal):
    """Create two users + return (user_a, user_b, make_client)."""
    user_a = _make_user(db, "Alice", "demo-member-002@company.example")
    user_b = _make_user(db, "Bob", "demo-member-003@company.example")

    # Capture ids so the closure survives session scope.
    a_id = user_a.id
    b_id = user_b.id

    def make_client(as_user: str):
        """Build a TestClient that auths as user A or B."""
        def _current_user():
            s = SessionLocal()
            try:
                uid = a_id if as_user == "A" else b_id
                return s.query(User).filter_by(id=uid).first()
            finally:
                s.close()

        app = _build_app(SessionLocal, _current_user)
        return TestClient(app)

    return user_a, user_b, make_client


# ── Tests ───────────────────────────────────────────────────────────────


def test_import_csv_creates_leads_owned_by_current_user(world):
    user_a, _user_b, make_client = world
    client = make_client("A")

    csv_bytes = (
        b"First Name,Last Name,Email,Company\n"
        b"Carol,Carter,carol@acme.com,Acme\n"
        b"Dan,Doyle,dan@beta.com,Beta\n"
        b"Eve,Evans,eve@gamma.com,Gamma\n"
    )
    resp = client.post(
        "/api/conference/import?conference_name=ADIPEC2026",
        files={"file": ("attendees.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["imported"] == 3
    assert data["skipped"] == 0

    # List leads via the API - should return 3 rows all owned by user A.
    resp = client.get("/api/conference/leads")
    assert resp.status_code == 200
    body = resp.json()
    leads = body.get("items", body) if isinstance(body, dict) else body
    assert len(leads) == 3
    assert {l["email"] for l in leads} == {
        "carol@acme.com",
        "dan@beta.com",
        "eve@gamma.com",
    }


def test_list_leads_scopes_to_current_user(world, db):
    user_a, user_b, make_client = world

    # Seed two leads: one owned by each user.
    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="ADIPEC2026",
            email="only-alice@example.com",
            first_name="A",
            user_id=user_a.id,
        )
    )
    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="ADIPEC2026",
            email="only-bob@example.com",
            first_name="B",
            user_id=user_b.id,
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.get("/api/conference/leads")
    assert resp.status_code == 200
    body = resp.json()
    leads = body.get("items", body) if isinstance(body, dict) else body
    assert [l["email"] for l in leads] == ["only-alice@example.com"]

    client_b = make_client("B")
    resp = client_b.get("/api/conference/leads")
    assert resp.status_code == 200
    body = resp.json()
    leads = body.get("items", body) if isinstance(body, dict) else body
    assert [l["email"] for l in leads] == ["only-bob@example.com"]


def test_conferences_lists_per_user_names_only(world, db):
    user_a, user_b, make_client = world

    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="Alice-Event",
            email="a@x.com",
            first_name="A",
            user_id=user_a.id,
        )
    )
    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="Bob-Event",
            email="b@x.com",
            first_name="B",
            user_id=user_b.id,
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.get("/api/conference/conferences")
    assert resp.status_code == 200
    assert resp.json() == ["Alice-Event"]

    client_b = make_client("B")
    resp = client_b.get("/api/conference/conferences")
    assert resp.status_code == 200
    assert resp.json() == ["Bob-Event"]


def test_delete_lead_forbidden_for_wrong_user(world, db):
    user_a, user_b, make_client = world

    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="ADIPEC2026",
            email="only-alice@example.com",
            first_name="A",
            user_id=user_a.id,
        )
    )
    db.commit()

    # User B tries to delete Alice's lead - must 403.
    client_b = make_client("B")
    resp = client_b.delete(f"/api/conference/leads/{lead_id}")
    assert resp.status_code == 403

    # Alice's lead must still exist.
    remaining = db.query(ConferenceLead).filter_by(id=lead_id).first()
    assert remaining is not None
    assert remaining.user_id == user_a.id


def test_generate_endpoint_saves_subject_and_body_with_mocked_anthropic(
    world, db, monkeypatch
):
    """POST /conference/generate - mock Anthropic and assert the lead row
    gets the generated subject + body written to it."""
    user_a, _user_b, make_client = world

    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="ADIPEC2026",
            email="carol@acme.com",
            first_name="Carol",
            company="Acme",
            user_id=user_a.id,
        )
    )
    db.commit()

    # Stub the anthropic client response used by _generate_email.
    # Note: we patch the *bound reference* inside service.py, not the
    # original in _shared.py - service.py imported the name at load time
    # so rebinding _shared's attribute is invisible from inside the service.
    from src.api.conference import service as _service_mod

    class _StubContent:
        def __init__(self, text):
            self.text = text

    class _StubUsage:
        input_tokens = 42
        output_tokens = 17

    class _StubMessage:
        def __init__(self, text):
            self.content = [_StubContent(text)]
            self.usage = _StubUsage()

    class _StubClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return _StubMessage(
                    '{"subject": "Great meeting you at ADIPEC", '
                    '"body": "Hey Carol, enjoyed the chat about refinery HSE."}'
                )

    monkeypatch.setattr(_service_mod, "get_anthropic_client", lambda: _StubClient())

    client_a = make_client("A")
    resp = client_a.post(
        "/api/conference/generate",
        json={"lead_id": lead_id, "context": "Met at ADIPEC, discussed HSE."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "Great meeting" in data["generated_subject"]
    assert "Carol" in data["generated_body"]

    # DB row mirrors the response.
    db.expire_all()
    row = db.query(ConferenceLead).filter_by(id=lead_id).first()
    assert row.generated_subject == "Great meeting you at ADIPEC"
    assert "Carol" in row.generated_body


def test_bulk_delete_scopes_to_current_user(world, db):
    user_a, user_b, make_client = world
    keep_id = str(uuid.uuid4())
    del_id_1 = str(uuid.uuid4())
    del_id_2 = str(uuid.uuid4())

    db.add(
        ConferenceLead(
            id=keep_id,
            conference_name="X",
            email="keep@x.com",
            first_name="Keep",
            user_id=user_b.id,  # owned by B
        )
    )
    db.add(
        ConferenceLead(
            id=del_id_1,
            conference_name="X",
            email="del1@x.com",
            first_name="D1",
            user_id=user_a.id,
        )
    )
    db.add(
        ConferenceLead(
            id=del_id_2,
            conference_name="X",
            email="del2@x.com",
            first_name="D2",
            user_id=user_a.id,
        )
    )
    db.commit()

    # User A bulk-deletes all three ids - only the two they own should go.
    client_a = make_client("A")
    resp = client_a.post(
        "/api/conference/leads/bulk-delete",
        json={"ids": [keep_id, del_id_1, del_id_2]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}

    # User B's lead still exists.
    assert db.query(ConferenceLead).filter_by(id=keep_id).first() is not None
    assert db.query(ConferenceLead).filter_by(id=del_id_1).first() is None
    assert db.query(ConferenceLead).filter_by(id=del_id_2).first() is None


def test_bulk_delete_empty_ids_returns_zero(world):
    _user_a, _user_b, make_client = world
    client_a = make_client("A")
    resp = client_a.post("/api/conference/leads/bulk-delete", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}


def test_save_email_writes_subject_and_body(world, db):
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="X",
            email="e@x.com",
            first_name="E",
            user_id=user_a.id,
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.patch(
        f"/api/conference/leads/{lead_id}/email",
        json={"subject": "Custom subject", "body": "Custom body text."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated_subject"] == "Custom subject"
    assert data["generated_body"] == "Custom body text."


def test_costs_endpoint_returns_breakdown(world, db):
    user_a, _user_b, make_client = world
    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="E1",
            email="a@x.com",
            first_name="A",
            user_id=user_a.id,
            tokens_input=1000,
            tokens_output=500,
        )
    )
    db.add(
        ConferenceLead(
            id=str(uuid.uuid4()),
            conference_name="E1",
            email="b@x.com",
            first_name="B",
            user_id=user_a.id,
            tokens_input=2000,
            tokens_output=1000,
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.get("/api/conference/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tokens_input"] == 3000
    assert data["total_tokens_output"] == 1500
    assert len(data["events"]) == 1
    assert data["events"][0]["event"] == "E1"
    assert data["events"][0]["leads"] == 2


def test_list_inboxes_always_includes_outlook_default(world):
    _user_a, _user_b, make_client = world
    client_a = make_client("A")
    resp = client_a.get("/api/conference/inboxes")
    assert resp.status_code == 200
    inboxes = resp.json()
    # The outlook pseudo-inbox is always first.
    assert inboxes[0]["id"] == "outlook"
    assert inboxes[0]["type"] == "outlook"


def test_add_inbox_rejects_imap_without_host_or_password(world):
    _user_a, _user_b, make_client = world
    client_a = make_client("A")
    resp = client_a.post(
        "/api/conference/inboxes",
        json={"name": "Work", "email": "me@co.com", "type": "imap"},
    )
    assert resp.status_code == 400


def test_add_and_delete_inbox_roundtrip(world):
    _user_a, _user_b, make_client = world
    client_a = make_client("A")

    resp = client_a.post(
        "/api/conference/inboxes",
        json={
            "name": "Work",
            "email": "me@co.com",
            "type": "imap",
            "imap_host": "imap.co.com",
            "imap_port": 993,
            "imap_password": "secret",
            "imap_use_ssl": True,
        },
    )
    assert resp.status_code == 200
    inbox_id = resp.json()["id"]

    # Listing returns the outlook default + our new IMAP inbox.
    resp = client_a.get("/api/conference/inboxes")
    assert resp.status_code == 200
    assert any(i["id"] == inbox_id for i in resp.json())

    # User B cannot see it.
    client_b = make_client("B")
    resp = client_b.get("/api/conference/inboxes")
    assert not any(i["id"] == inbox_id for i in resp.json())

    # User B cannot delete it.
    resp = client_b.delete(f"/api/conference/inboxes/{inbox_id}")
    assert resp.status_code == 403

    # User A deletes it successfully.
    resp = client_a.delete(f"/api/conference/inboxes/{inbox_id}")
    assert resp.status_code == 200


def test_draft_sequence_step_404_for_cross_user_lead(world, db, monkeypatch):
    """User B cannot push a draft for User A's lead."""
    user_a, _user_b, make_client = world

    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="ADIPEC2026",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
            generated_sequence='{"email1": {"subject": "Hi", "body": "Test body"}}',
        )
    )
    db.commit()

    # Mock _dispatch_draft so we never touch Outlook even on the success path.
    from src.api.conference import routes_drafts as _drafts_mod
    from src.api.conference import service as _service_mod

    def _fake_dispatch(*args, **kwargs):
        return {"ok": True, "message_id": "stub"}

    monkeypatch.setattr(_service_mod, "_dispatch_draft", _fake_dispatch)
    monkeypatch.setattr(_drafts_mod, "_dispatch_draft", _fake_dispatch)

    # User B's access must be refused with 403 (fail-closed ownership guard).
    client_b = make_client("B")
    resp = client_b.post(f"/api/conference/draft-sequence/{lead_id}?step=email1")
    assert resp.status_code == 403


def test_draft_endpoint_happy_path_with_mocked_dispatch(world, db, monkeypatch):
    """POST /conference/draft/{lead_id} - mock the Outlook dispatch and
    assert the draft_message_id + draft_created_at get populated."""
    user_a, _user_b, make_client = world

    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
            generated_subject="Hey Carol",
            generated_body="Enjoyed the chat.",
        )
    )
    db.commit()

    from src.api.conference import routes_drafts as _drafts_mod

    def _fake_dispatch(*args, **kwargs):
        return {"ok": True, "message_id": "mock-msg-abc"}

    monkeypatch.setattr(_drafts_mod, "_dispatch_draft", _fake_dispatch)

    client_a = make_client("A")
    resp = client_a.post(f"/api/conference/draft/{lead_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["draft_message_id"] == "mock-msg-abc"
    assert data["draft_created_at"] is not None


def test_draft_endpoint_rejects_when_no_generated_email(world, db):
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
        )
    )
    db.commit()
    client_a = make_client("A")
    resp = client_a.post(f"/api/conference/draft/{lead_id}")
    assert resp.status_code == 400


def test_clear_draft_resets_tracking_fields(world, db):
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    from datetime import datetime, timezone
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
            draft_message_id="some-msg-id",
            draft_created_at=datetime.now(timezone.utc),
            generated_sequence='{"email1": {"subject": "Hi", "body": "Hey"}, "_drafted_steps": ["email1"]}',
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.post(f"/api/conference/leads/{lead_id}/clear-draft")
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft_message_id"] is None
    assert data["draft_created_at"] is None
    # _drafted_steps marker is gone from the serialised sequence.
    assert "_drafted_steps" not in (data["generated_sequence"] or {})


def test_generate_sequence_endpoint_saves_full_sequence(world, db, monkeypatch):
    """POST /conference/generate-sequence - mock _generate_sequence and
    assert generated_sequence, email1 fields, and tokens are persisted."""
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
        )
    )
    db.commit()

    fake_seq = {
        "email1": {"subject": "Hey Carol", "body": "Enjoyed meeting you.", "word_count": 3},
        "email2": {"subject": "Quick thought", "body": "Share an idea.", "word_count": 3},
        "linkedin_connect_note": "Nice meeting you, Carol.",
        "linkedin_message": "Hey Carol, following our chat.",
        "_validation": {"passed": True, "warnings": []},
        "_tokens": {"input": 123, "output": 45},
    }

    from src.api.conference import routes_generate as _rg

    def _fake_generate_sequence(lead, context, db, **kwargs):
        return dict(fake_seq)

    monkeypatch.setattr(_rg, "_generate_sequence", _fake_generate_sequence)

    client_a = make_client("A")
    resp = client_a.post(
        "/api/conference/generate-sequence",
        json={"lead_id": lead_id, "context": "Met at ADIPEC"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["generated_subject"] == "Hey Carol"
    assert data["generated_sequence"]["email1"]["subject"] == "Hey Carol"
    assert data["generated_sequence"]["email2"]["subject"] == "Quick thought"

    # Verify tokens were atomically added to the row.
    db.expire_all()
    row = db.query(ConferenceLead).filter_by(id=lead_id).first()
    assert row.tokens_input == 123
    assert row.tokens_output == 45


def test_save_sequence_email_updates_one_step(world, db):
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    import json as _json
    initial = {
        "email1": {"subject": "Orig1", "body": "Orig body 1"},
        "email2": {"subject": "Orig2", "body": "Orig body 2"},
    }
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
            generated_sequence=_json.dumps(initial),
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.patch(
        f"/api/conference/leads/{lead_id}/sequence-email",
        json={"step": "email2", "subject": "New subject", "body": "New body"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated_sequence"]["email2"]["subject"] == "New subject"
    assert data["generated_sequence"]["email2"]["body"] == "New body"
    # email1 is untouched.
    assert data["generated_sequence"]["email1"]["subject"] == "Orig1"


def test_save_sequence_email_rejects_invalid_step(world, db):
    user_a, _user_b, make_client = world
    lead_id = str(uuid.uuid4())
    db.add(
        ConferenceLead(
            id=lead_id,
            conference_name="E",
            email="carol@acme.com",
            first_name="Carol",
            user_id=user_a.id,
            generated_sequence='{"email1": {"subject": "Hi", "body": "Hey"}}',
        )
    )
    db.commit()

    client_a = make_client("A")
    resp = client_a.patch(
        f"/api/conference/leads/{lead_id}/sequence-email",
        json={"step": "email99", "subject": "X", "body": "Y"},
    )
    assert resp.status_code == 400


def test_generate_not_found_returns_404(world):
    _user_a, _user_b, make_client = world
    client_a = make_client("A")
    resp = client_a.post(
        "/api/conference/generate",
        json={"lead_id": "nonexistent-id", "context": "anything"},
    )
    assert resp.status_code == 404
