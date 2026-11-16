"""
Tests for newsletter template engine (Field Notes template).

Covers: base template rendering, email-client compatibility, HMAC unsubscribe
links, owner name rendering, XSS escaping, and regressions from Sessions 1-2.

Updated after template simplification: block-based functions (hero_block,
stats_block, etc.) and preset constants (PROSPECT_DEFAULT_CONTENT,
CLIENT_DEFAULT_CONTENT) were removed.  Tests now target the surviving API:
render_newsletter, preview_newsletter, build_unsubscribe_url,
generate_unsubscribe_token, _field_notes_template, _plain_text_to_html.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("NEWSLETTER_HMAC_SECRET", "test-hmac-secret-for-unit-tests")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.database import Base, get_db
from src.db.models import (
    Newsletter,
    NewsletterRecipient,
    NewsletterRecipientStatus,
    NewsletterStatus,
    NewsletterTrack,
    NewsletterUnsubscribe,
    Prospect,
    ProspectStatus,
    UnsubscribeEntry,
    User,
    UserRole,
)
from src.services.newsletter_templates import (
    build_unsubscribe_url,
    generate_unsubscribe_token,
    preview_newsletter,
    render_newsletter,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Create in-memory SQLite engine."""
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


def _make_user(db, uid=None, name="Test Sender", email=None):
    uid = uid or str(uuid.uuid4())
    email = email or "{}@test.local".format(uid)
    u = User(
        id=uid,
        name=name,
        email=email,
        password_hash="fakehash",
        role=UserRole.admin,
    )
    db.add(u)
    db.commit()
    return u


def _make_newsletter(db, user, track=NewsletterTrack.prospect, title="Test NL",
                     subject_line="Test Subject", content_json=None):
    nl = Newsletter(
        id=str(uuid.uuid4()),
        title=title,
        track=track,
        owner_id=user.id,
        subject_line=subject_line,
        preheader_text="Preview text here",
        content_json=content_json or {
            "body_text": "Hello {{name}},\n\nThis is a test newsletter.\n\nBest regards",
        },
    )
    db.add(nl)
    db.commit()
    return nl


def _make_prospect(db, user, email, status=ProspectStatus.new,
                   first_name="Test", last_name="Person", company="TestCorp"):
    p = Prospect(
        id=str(uuid.uuid4()),
        user_id=user.id,
        email=email,
        first_name=first_name,
        last_name=last_name,
        company=company,
        status=status,
    )
    db.add(p)
    db.commit()
    return p


# ── Test 1: Base template renders valid HTML with required boilerplate ───────


class TestBaseTemplateBoilerplate:
    def test_doctype_present(self, db):
        """Rendered HTML starts with DOCTYPE."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id, recipient_email="r@test.local")
        assert html.startswith("<!DOCTYPE html>")

    def test_xmlns_namespaces(self, db):
        """HTML tag includes xmlns:v and xmlns:o namespaces."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        assert 'xmlns:v="urn:schemas-microsoft-com:vml"' in html
        assert 'xmlns:o="urn:schemas-microsoft-com:office:office"' in html

    def test_office_document_settings(self, db):
        """OfficeDocumentSettings block is present for Outlook."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        assert "OfficeDocumentSettings" in html
        assert "AllowPNG" in html
        assert "PixelsPerInch" in html

    def test_style_reset_present(self, db):
        """CSS reset is included in style block."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        assert "border-collapse:collapse" in html
        assert "mso-table-lspace" in html


# ── Test 2: Email-client compatibility ──────────────────────────────────────


class TestEmailClientCompatibility:
    def test_no_flex_in_rendered_content(self, db):
        """No flexbox anywhere in rendered HTML (outside MSO comments)."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        clean = re.sub(r'<!--\[if.*?\]>.*?<!\[endif\]-->', '', html, flags=re.DOTALL)
        assert "display:flex" not in clean
        assert "display: flex" not in clean

    def test_no_grid_in_rendered_content(self, db):
        """No CSS grid anywhere in rendered HTML."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        clean = re.sub(r'<!--\[if.*?\]>.*?<!\[endif\]-->', '', html, flags=re.DOTALL)
        assert "display:grid" not in clean
        assert "display: grid" not in clean

    def test_tables_have_border_collapse(self, db):
        """All <table> tags include border-collapse in their style."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        tables = re.findall(r'<table[^>]*style="([^"]*)"[^>]*>', html)
        for style in tables:
            assert "border-collapse" in style, "Table missing border-collapse: {}".format(style[:80])

    def test_inline_styles_on_td_elements(self, db):
        """All <td> elements have inline style attributes."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        td_tags = re.findall(r'<td[^>]*>', html)
        for td in td_tags:
            assert 'style="' in td, "TD missing inline style: {}".format(td[:80])


# ── Test 3: Unsubscribe URL with HMAC token ─────────────────────────────────


class TestUnsubscribeHMAC:
    def test_unsubscribe_link_in_rendered_html(self, db):
        """Rendered HTML contains an unsubscribe link with token."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id, recipient_email="recipient@corp.local")
        assert "unsubscribe" in html.lower()
        assert "token=" in html
        assert "email=recipient" in html

    def test_hmac_token_is_deterministic(self):
        """Same email always produces the same token."""
        t1 = generate_unsubscribe_token("test@corp.local")
        t2 = generate_unsubscribe_token("test@corp.local")
        assert t1 == t2

    def test_hmac_token_differs_per_email(self):
        """Different emails produce different tokens."""
        t1 = generate_unsubscribe_token("alice@corp.local")
        t2 = generate_unsubscribe_token("bob@corp.local")
        assert t1 != t2

    def test_unsubscribe_url_format(self):
        """Unsubscribe URL has expected format."""
        url = build_unsubscribe_url("test@corp.local")
        assert "/api/newsletters/unsubscribe" in url
        assert "token=" in url
        assert "email=test" in url

    def test_preview_uses_placeholder_email(self, db):
        """Preview rendering uses placeholder unsubscribe email."""
        user = _make_user(db)
        nl = _make_newsletter(db, user)
        html = preview_newsletter(db, nl.id)
        assert "preview%40example.com" in html


# ── Test 4: Owner name rendering ────────────────────────────────────────────
# Note: The Field Notes template uses COMPANY_NAME in the footer, not the
# owner's name.  These tests verify that render_newsletter completes without
# error for different owners and that the output differs when content differs.


class TestOwnerNameRendering:
    def test_render_completes_for_owner(self, db):
        """render_newsletter completes without error."""
        user = _make_user(db, name="Alice Johnson")
        nl = _make_newsletter(db, user)
        html = render_newsletter(db, nl.id)
        # Should produce valid HTML
        assert "<!DOCTYPE html>" in html

    def test_different_owners_render_independently(self, db):
        """Different owners can render their own newsletters."""
        u1 = _make_user(db, uid="u-owner-1", name="Bob Smith", email="bob@test.local")
        u2 = _make_user(db, uid="u-owner-2", name="Carol Davis", email="carol@test.local")

        nl1 = _make_newsletter(db, u1, title="NL1", subject_line="Subject One")
        nl2 = _make_newsletter(db, u2, title="NL2", subject_line="Subject Two")

        html1 = render_newsletter(db, nl1.id)
        html2 = render_newsletter(db, nl2.id)

        # Both render successfully
        assert "<!DOCTYPE html>" in html1
        assert "<!DOCTYPE html>" in html2
        # Different subjects produce different output
        assert "Subject One" in html1
        assert "Subject Two" in html2


# ── Test 5: XSS escaping ───────────────────────────────────────────────────


class TestXSSEscaping:
    def test_html_escaping_in_body(self, db):
        """Body text with HTML entities is properly escaped."""
        user = _make_user(db)
        nl = _make_newsletter(db, user, content_json={
            "body_text": "<script>alert(1)</script> & A < B",
        })
        html = render_newsletter(db, nl.id)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&amp;" in html

    def test_subject_line_escaping(self, db):
        """Subject line with special chars is escaped in output."""
        user = _make_user(db)
        nl = _make_newsletter(db, user, subject_line='Test "quotes" & <tags>')
        html = render_newsletter(db, nl.id)
        assert "<tags>" not in html
        assert "&lt;tags&gt;" in html


# ── Regression tests ────────────────────────────────────────────────────────


class TestRegressions:
    """Re-verify critical tests from Sessions 1 and 2."""

    def test_session2_populate_via_service(self, db, SessionLocal):
        """Session 2 Test 4 regression: populate_recipients returns correct stats."""
        user = _make_user(db, uid="reg-user-1", email="reg1@test.local")
        for i in range(3):
            _make_prospect(db, user, "e{}@corp.local".format(i), ProspectStatus.enrolled)

        nl = _make_newsletter(db, user)

        from src.api.newsletters import populate_recipients
        stats = populate_recipients(db, nl.id, user)

        assert stats["added"] == 3
        assert stats["total"] == 3
        assert stats["skipped_duplicates"] == 0

    def test_session1_is_suppressed_positive(self, db):
        """Session 1 Test 3 regression: is_suppressed returns True for unsubscribed."""
        unsub = NewsletterUnsubscribe(
            id=str(uuid.uuid4()),
            email="gone@corp.local",
            reason="manual",
        )
        db.add(unsub)
        db.commit()

        assert NewsletterUnsubscribe.is_suppressed(db, "gone@corp.local") is True

    def test_session1_is_suppressed_negative(self, db):
        """Session 1 Test 3 regression: is_suppressed returns False for non-suppressed."""
        assert NewsletterUnsubscribe.is_suppressed(db, "active@corp.local") is False


# ── Test: No hardcoded emails or company names in templates ──────────────────


class TestNoHardcodes:
    def test_company_name_from_env(self):
        """COMPANY_NAME is sourced from env, not hardcoded."""
        from src.services import newsletter_templates as nt
        assert hasattr(nt, "COMPANY_NAME")
        import inspect
        source = inspect.getsource(nt)
        assert 'os.getenv("NEWSLETTER_COMPANY_NAME"' in source

    def test_hmac_secret_from_env(self):
        """HMAC secret is sourced from env, not hardcoded."""
        from src.services import newsletter_templates as nt
        import inspect
        source = inspect.getsource(nt)
        assert 'os.getenv("NEWSLETTER_HMAC_SECRET"' in source
        assert 'os.getenv("JWT_SECRET"' in source

    def test_unsubscribe_base_url_from_env(self):
        """Unsubscribe base URL is configurable via env."""
        from src.services import newsletter_templates as nt
        import inspect
        source = inspect.getsource(nt)
        assert "NEWSLETTER_UNSUBSCRIBE_BASE_URL" in source
