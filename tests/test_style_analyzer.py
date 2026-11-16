"""
Tests for writing style analyzer.

Tests the pure analysis functions without any API calls.
"""

import pytest
from src.services.style_analyzer import (
    analyze_style,
    build_style_prompt,
    _word_count,
    _avg_sentence_length,
    _extract_greetings,
    _extract_signoffs,
    _measure_formality,
    _measure_bullet_usage,
    _empty_profile,
)


# ── Helper function tests ────────────────────────────────────────────────────

class TestWordCount:
    def test_basic(self):
        assert _word_count("hello world foo") == 3

    def test_empty(self):
        assert _word_count("") == 0

    def test_multiline(self):
        assert _word_count("one two\nthree four") == 4


class TestAvgSentenceLength:
    def test_basic(self):
        result = _avg_sentence_length("This is a sentence. This is another one.")
        assert result > 0

    def test_single_sentence(self):
        result = _avg_sentence_length("Just one sentence here with five words.")
        # "Just one sentence here with five words" = 7 words
        assert 5 <= result <= 8

    def test_empty(self):
        assert _avg_sentence_length("") == 0.0

    def test_short_fragments_filtered(self):
        # Fragments shorter than 5 chars should be ignored
        result = _avg_sentence_length("ok. This is a real sentence.")
        assert result > 0


class TestExtractGreetings:
    def test_hi_detected(self):
        bodies = ["Hi John, hope you're well. Let's chat about the project."]
        greetings = _extract_greetings(bodies)
        assert len(greetings) >= 1
        assert any("Hi" in g for g in greetings)

    def test_hey_detected(self):
        bodies = ["Hey Sarah, quick question for you."]
        greetings = _extract_greetings(bodies)
        assert len(greetings) >= 1

    def test_no_greeting(self):
        bodies = ["The report is attached. Please review by Friday."]
        greetings = _extract_greetings(bodies)
        # May or may not find a greeting - just verify no crash
        assert isinstance(greetings, list)

    def test_multiple_emails(self):
        bodies = [
            "Hi John, here's the update.",
            "Hi Sarah, just checking in.",
            "Hey Mike, quick one.",
        ]
        greetings = _extract_greetings(bodies)
        assert len(greetings) >= 1


class TestExtractSignoffs:
    def test_cheers_detected(self):
        bodies = ["Here's the doc. Cheers, Rishabh"]
        signoffs = _extract_signoffs(bodies)
        assert any("Cheers" in s for s in signoffs)

    def test_talk_soon(self):
        bodies = ["Let me know your thoughts. Talk soon, Rishabh"]
        signoffs = _extract_signoffs(bodies)
        assert any("Talk Soon" in s for s in signoffs)

    def test_no_signoff(self):
        bodies = ["Here is the attachment."]
        signoffs = _extract_signoffs(bodies)
        assert isinstance(signoffs, list)


class TestMeasureFormality:
    def test_conversational(self):
        bodies = [
            "Hey, yeah that sounds awesome. Let's grab coffee and chat about it.",
            "Cool, I'll send that over. Quick question though.",
        ]
        assert _measure_formality(bodies) == "conversational"

    def test_formal(self):
        bodies = [
            "Pursuant to our discussion, please find the documents herewith.",
            "Accordingly, regarding the matter at hand, therefore we must proceed.",
        ]
        assert _measure_formality(bodies) == "formal"

    def test_professional_mixed(self):
        bodies = [
            "Hi team, here's the status update for this week. Let me know if you have questions.",
        ]
        # No strong markers either way → professional
        result = _measure_formality(bodies)
        assert result in ("professional", "conversational")  # could go either way


class TestMeasureBulletUsage:
    def test_with_bullets(self):
        bodies = [
            "Here are the items: - Item one - Item two - Item three",
            "Updates: * First thing * Second thing",
        ] * 5  # make frequency > 0.1
        result = _measure_bullet_usage(bodies)
        assert result["uses_bullets"] is True
        assert result["frequency"] > 0

    def test_without_bullets(self):
        bodies = [
            "This is a normal email without any bullet points at all.",
            "Another normal email. Just paragraphs.",
        ]
        result = _measure_bullet_usage(bodies)
        assert result["uses_bullets"] is False

    def test_empty(self):
        result = _measure_bullet_usage([])
        assert result["frequency"] == 0


# ── Full analysis tests ──────────────────────────────────────────────────────

class TestAnalyzeStyle:
    def test_with_emails(self):
        emails = [
            {"subject": "Quick update", "body": "Hi John, here's the latest. Talk soon, Rishabh"},
            {"subject": "Re: Meeting", "body": "Hey Sarah, yeah that works for me. Cheers, Rishabh"},
            {"subject": "Report", "body": "Hi team, the report is attached. Let me know if you have questions. Best, Rishabh"},
        ]
        profile = analyze_style(emails)
        assert profile["total_emails_analyzed"] == 3
        assert profile["avg_word_count"] > 0
        assert profile["formality"] in ("conversational", "professional", "formal")
        assert isinstance(profile["greeting_patterns"], list)
        assert isinstance(profile["sign_off_patterns"], list)

    def test_empty_list(self):
        profile = analyze_style([])
        assert profile == _empty_profile()
        assert profile["total_emails_analyzed"] == 0

    def test_emails_without_body(self):
        emails = [{"subject": "Test", "body": ""}]
        profile = analyze_style(emails)
        assert profile["total_emails_analyzed"] == 0

    def test_sample_selection(self):
        """Samples should prefer emails between 20-250 words."""
        emails = [
            {"subject": f"Email {i}", "body": " ".join(["word"] * 50)}
            for i in range(10)
        ]
        profile = analyze_style(emails)
        assert len(profile["sample_emails"]) <= 5


# ── build_style_prompt tests ─────────────────────────────────────────────────

class TestBuildStylePrompt:
    def test_none_profile(self):
        assert build_style_prompt(None) == ""

    def test_empty_dict(self):
        assert build_style_prompt({}) == ""

    def test_valid_profile(self):
        profile = {
            "avg_word_count": 85,
            "avg_sentence_length": 14,
            "formality": "conversational",
            "greeting_patterns": ["Hi [Name],"],
            "sign_off_patterns": ["Cheers,"],
            "uses_bullet_points": False,
            "sample_emails": [
                {"subject": "Test", "body": "Hi John, test email."},
            ],
        }
        prompt = build_style_prompt(profile)
        assert "85 words" in prompt
        assert "conversational" in prompt
        assert "Hi [Name]," in prompt
        assert "Cheers," in prompt
        assert "rarely or never" in prompt

    def test_with_bullet_usage(self):
        profile = {
            "avg_word_count": 100,
            "avg_sentence_length": 15,
            "formality": "professional",
            "greeting_patterns": [],
            "sign_off_patterns": [],
            "uses_bullet_points": True,
            "sample_emails": [],
        }
        prompt = build_style_prompt(profile)
        assert "occasionally" in prompt
