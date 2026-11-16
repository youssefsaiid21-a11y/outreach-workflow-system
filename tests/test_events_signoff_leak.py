"""
Regression tests for the Nehal/Vishnu greeting leak (2026-04-09).

Background - incident postmortem:
  Nehal sent 9 conference-outreach emails that ended with
  ``Talk soon,\\nVishnu`` or ``Talk soon,\\nVishnu Saran``. Vishnu was a
  colleague whose email happened to be sitting in Youssef's style-synced
  sample_emails as a raw "Dear Izad, ... Thanks and Regards, Vishnu
  Saran" example. When the style block was injected into Claude's
  prompt, Claude echoed the name from that example even though the
  ``sender_name`` parameter said "Nehal".

This file pins three layers of defence so the bug cannot come back:

  1. ``_sanitize_sample_body`` strips the trailing sign-off (and any name
     that follows) from raw sample emails before they're embedded in the
     generation prompt - no verbatim names can leak into the prompt.
  2. ``build_style_prompt`` uses the sanitiser on every sample and skips
     samples that were entirely a sign-off.
  3. ``_strip_signoff`` at draft time removes the closer block from
     already-generated bodies so Outlook's auto-signature can handle
     sign-off alone - this also cleans up historical leads that were
     generated before the prompt-side fix.

Plus:

  4. The Outlook ``create_draft`` payload no longer sets ``isRead: True``
     so Outlook's own signature is appended normally when the user opens
     the draft.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from src.api.conference._shared import _strip_greeting, _strip_signoff
from src.services.style_analyzer import _sanitize_sample_body, build_style_prompt


# ── _strip_signoff ──────────────────────────────────────────────────────


def test_strip_signoff_removes_talk_soon_plus_name():
    """The exact Nehal incident shape: closer + name on two lines."""
    body = (
        "Hi Virender,\n\n"
        "Great meeting you at the conference. Your work on predictive "
        "maintenance at Hughes sounds fascinating.\n\n"
        "Would you have 20 minutes for a quick call this week or next?\n\n"
        "Talk soon,\n"
        "Vishnu"
    )
    out = _strip_signoff(body)
    assert "Vishnu" not in out
    assert "Talk soon" not in out
    assert "20 minutes for a quick call" in out
    # The last non-empty line should be the CTA, not a dangling blank.
    assert out.rstrip().endswith("?")


def test_strip_signoff_removes_talk_soon_plus_full_name():
    """Two-word name variant - 'Talk soon,\\nVishnu Saran'."""
    body = (
        "Hi Shimjith,\n\n"
        "Happy to work around your schedule.\n\n"
        "Talk soon,\n"
        "Vishnu Saran"
    )
    out = _strip_signoff(body)
    assert "Vishnu" not in out
    assert "Saran" not in out
    assert "work around your schedule" in out


def test_strip_signoff_removes_thanks_and_regards_block():
    """Handles the 'Thanks and Regards, Vishnu Saran' on one line form."""
    body = (
        "Dear Izad,\n\n"
        "It is SGD, we can confirm that.\n\n"
        "Thanks and Regards, Vishnu Saran"
    )
    out = _strip_signoff(body)
    assert "Vishnu" not in out
    assert "Thanks" not in out
    assert "SGD" in out


def test_strip_signoff_leaves_body_alone_when_no_closer():
    """No closer line → body unchanged. Must not accidentally strip
    a real closing sentence."""
    body = (
        "Hi John,\n\n"
        "Great meeting you at GITEX. I'd love to learn more about your "
        "integration plans for Q3.\n\n"
        "Is there a good time this week to jump on a call?"
    )
    out = _strip_signoff(body)
    assert out == body


def test_strip_signoff_handles_empty_body():
    assert _strip_signoff("") == ""
    assert _strip_signoff("   \n\n  ") == "   \n\n  "


def test_strip_signoff_preserves_main_content_when_only_name_exists():
    """A bare trailing name without a closer is ambiguous - could be a
    signature line someone added manually, or a legitimate short
    sentence. Conservative: leave it alone rather than risk stripping a
    sentence. We only strip when a real closer word is present."""
    body = "Hi John,\n\nThe demo is ready.\n\nVishnu"
    out = _strip_signoff(body)
    # No closer word → conservative pass-through.
    assert out == body


def test_strip_signoff_does_not_swallow_body_that_ends_in_question():
    body = "Hey Sam,\n\nAre you free Thursday for a quick chat?"
    assert _strip_signoff(body) == body


def test_strip_signoff_handles_cheers_with_first_name():
    body = "Hi Lee,\n\nGreat seeing you. Want to grab coffee next week?\n\nCheers,\nNehal"
    out = _strip_signoff(body)
    assert "Nehal" not in out
    assert "Cheers" not in out
    assert "grab coffee next week" in out


def test_strip_signoff_handles_looking_forward():
    body = (
        "Hi Ramesh,\n\n"
        "Happy to share the case study I mentioned.\n\n"
        "Looking forward,\n"
        "Vishnu"
    )
    out = _strip_signoff(body)
    assert "Vishnu" not in out
    assert "case study" in out


def test_strip_signoff_and_strip_greeting_compose_cleanly():
    """The two helpers must be safe to use together - strip_greeting
    removes the opening, strip_signoff removes the closer, and the body
    between them survives."""
    body = (
        "Hi Ahmed,\n\n"
        "Loved your panel on process safety at ADIPEC.\n\n"
        "Want to compare notes on how other operators have approached "
        "similar requirements?\n\n"
        "Talk soon,\n"
        "Vishnu"
    )
    out = _strip_greeting(_strip_signoff(body))
    assert "Hi Ahmed" not in out
    assert "Vishnu" not in out
    assert "ADIPEC" in out
    assert "compare notes" in out


# ── _sanitize_sample_body (style_analyzer) ──────────────────────────────


def test_sanitize_sample_removes_trailing_signoff_with_name():
    """The exact Vishnu sample from prod - must come back without the
    'Thanks and Regards, Vishnu Saran' tail."""
    body = (
        "Dear Izad, It is SGD, we can confirm that. We only have a SGD "
        "account. Thanks and Regards, Vishnu Saran"
    )
    out = _sanitize_sample_body(body)
    assert "Vishnu" not in out
    assert "Saran" not in out
    assert "Thanks" not in out
    assert "Regards" not in out
    # The content up to the closer survives.
    assert "SGD" in out


def test_sanitize_sample_handles_multi_line_body():
    body = (
        "Hi Mark,\n\n"
        "Following up on our conversation about the pilot.\n"
        "Let me know a good time.\n\n"
        "Best regards,\n"
        "Vishnu"
    )
    out = _sanitize_sample_body(body)
    assert "Vishnu" not in out
    assert "Best regards" not in out
    assert "pilot" in out
    assert "good time" in out


def test_sanitize_sample_passes_through_when_no_signoff():
    body = "Hi John, Just a quick note to say the report is in the drive."
    out = _sanitize_sample_body(body)
    assert out == body


def test_sanitize_sample_handles_empty_body():
    assert _sanitize_sample_body("") == ""
    assert _sanitize_sample_body(None) is None  # type: ignore[arg-type]


# ── build_style_prompt ──────────────────────────────────────────────────


def test_build_style_prompt_strips_vishnu_from_samples():
    """End-to-end: a profile with Vishnu in its sample_emails must
    never produce a prompt containing that name. This is the exact
    Nehal/Vishnu repro."""
    profile = {
        "avg_word_count": 42,
        "avg_sentence_length": 11,
        "formality": "conversational",
        "greeting_patterns": ["Dear [Name],", "Hi [Name],"],
        "sign_off_patterns": ["Thanks", "Regards"],
        "uses_bullet_points": False,
        "sample_emails": [
            {
                "subject": "Re: [Example Technologies] Proof of Concept",
                "body": (
                    "Dear Izad, It is SGD, we can confirm that. We only have "
                    "a SGD account. Thanks and Regards, Vishnu Saran"
                ),
            },
        ],
    }
    out = build_style_prompt(profile)
    assert "Vishnu" not in out
    assert "Saran" not in out
    assert "SGD" in out  # content still surfaces
    # The profile header survives
    assert "Writing style profile" in out
    # The sanitisation note is visible so future maintainers understand.
    assert "sign-off blocks removed" in out


def test_build_style_prompt_skips_sample_that_was_entirely_signoff():
    """If a sample was a one-line 'Thanks, Vishnu' confirmation, the
    whole sanitised body is empty - it must be SKIPPED, not injected
    as an empty 'Example 1:' stub."""
    profile = {
        "sample_emails": [
            {"subject": "Thanks!", "body": "Thanks, Vishnu"},
            {"subject": "A real one", "body": "Hi Mark, the report is in the drive."},
        ],
    }
    out = build_style_prompt(profile)
    assert "Vishnu" not in out
    assert "report is in the drive" in out
    # Only one example should have been rendered.
    assert out.count("Example") == 1


def test_build_style_prompt_empty_on_none_profile():
    assert build_style_prompt(None) == ""
    assert build_style_prompt({}) == ""


# ── outlook.create_draft payload ────────────────────────────────────────


def test_outlook_create_draft_no_longer_suppresses_signature():
    """The payload sent to Graph must NOT set isRead=True anymore.

    Before the fix, create_draft set isRead=True so Outlook wouldn't
    append its own signature - on the (wrong) assumption that the AI
    already wrote a sign-off. The new contract: the AI writes at most
    a closer line with no name, and Outlook's configured auto-signature
    fills in the name/title/contact when the draft is opened.

    This test stubs the HTTP post and inspects the payload that would
    be sent - NOT the response - so no network call is made.
    """
    from unittest.mock import patch
    from src.services.outlook import OutlookClient

    client = OutlookClient.__new__(OutlookClient)  # bypass __init__

    captured = {}

    def fake_post(url, payload, retries=3):
        captured["url"] = url
        captured["payload"] = payload
        return {"id": "mocked", "webLink": "https://outlook"}

    client._post = fake_post  # type: ignore[method-assign]

    client.create_draft(
        to_email="lead@example.com",
        to_name="Lead Name",
        subject="Hi",
        body="Hey, quick note.\n\nTalk soon,",
    )

    assert "payload" in captured
    payload = captured["payload"]
    # The critical assertion - isRead is NOT in the payload, or if it
    # ever is, it must be False. The old bug was isRead=True.
    assert payload.get("isRead") is not True, (
        "outlook.create_draft must not set isRead=True - that suppresses "
        "Outlook's auto-signature. See Nehal/Vishnu fix 2026-04-09."
    )


# ── _outlook_token_file split-brain fix ────────────────────────────────


def test_outlook_token_file_prefers_canonical_oauth_path(tmp_path, monkeypatch):
    """The 2026-04-09 split-brain fix: the conference reader must look at
    the SAME path that outlook_auth.py writes to - i.e.
    ``<repo>/token_cache_outlook_{user_id}.json`` - not the old
    ``<repo>/tokens/outlook_{user_id}.json`` location that never existed
    on prod.

    Previously, clicking "Connect Outlook" succeeded (token written at
    canonical path) but every subsequent draft returned 500 "Outlook is
    not connected" because the reader looked at a different, empty path.
    This test pins the fix: when the canonical file exists, the reader
    returns it, even if the legacy path is empty.
    """
    from src.api.conference import _shared

    # Fake a repo root by patching the module-level Path computation.
    fake_repo = tmp_path / "outreach-system"
    fake_repo.mkdir()

    # Write a canonical token file at the OAuth writer's path.
    user_id = "user-abc"
    canonical = fake_repo / f"token_cache_outlook_{user_id}.json"
    canonical.write_text('{"fake": "cache"}')

    # Monkeypatch the Path(__file__).parent chain to return our fake repo.
    fake_shared = fake_repo / "src" / "api" / "conference" / "_shared.py"
    fake_shared.parent.mkdir(parents=True)
    fake_shared.write_text("")  # just needs to exist for .resolve()

    class FakeUser:
        id = user_id

    monkeypatch.setattr(_shared, "__file__", str(fake_shared))
    result = _shared._outlook_token_file(FakeUser())
    assert result == canonical, (
        f"Expected canonical path {canonical}, got {result}. "
        "The reader must match outlook_auth.py's writer path."
    )


def test_outlook_token_file_falls_back_to_legacy_tokens_subdir(tmp_path, monkeypatch):
    """If a user connected via the older style-sync path (which wrote to
    ``tokens/outlook_{user_id}.json``), we keep honouring that file so
    they don't have to re-authenticate."""
    from src.api.conference import _shared

    fake_repo = tmp_path / "outreach-system"
    fake_repo.mkdir()
    (fake_repo / "tokens").mkdir()

    user_id = "user-def"
    legacy = fake_repo / "tokens" / f"outlook_{user_id}.json"
    legacy.write_text('{"legacy": "cache"}')

    fake_shared = fake_repo / "src" / "api" / "conference" / "_shared.py"
    fake_shared.parent.mkdir(parents=True)
    fake_shared.write_text("")

    class FakeUser:
        id = user_id

    monkeypatch.setattr(_shared, "__file__", str(fake_shared))
    result = _shared._outlook_token_file(FakeUser())
    assert result == legacy, (
        "When only the legacy path has a token file, it should be returned "
        "so pre-P3-3 users don't get forced through a fresh OAuth flow."
    )


def test_outlook_token_file_returns_canonical_when_neither_exists(tmp_path, monkeypatch):
    """For a brand-new user with no token yet, return the canonical path
    so a fresh OAuth will land in the right place and subsequent reads
    will find it."""
    from src.api.conference import _shared

    fake_repo = tmp_path / "outreach-system"
    fake_repo.mkdir()

    user_id = "user-ghi"
    fake_shared = fake_repo / "src" / "api" / "conference" / "_shared.py"
    fake_shared.parent.mkdir(parents=True)
    fake_shared.write_text("")

    class FakeUser:
        id = user_id

    monkeypatch.setattr(_shared, "__file__", str(fake_shared))
    result = _shared._outlook_token_file(FakeUser())
    assert result == fake_repo / f"token_cache_outlook_{user_id}.json"
    # And neither file exists, so a downstream .exists() check will
    # correctly tell the user to connect Outlook.
    assert not result.exists()
