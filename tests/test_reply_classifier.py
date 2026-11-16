"""
Tests for reply classification heuristics (no API calls).

The heuristic checks run before Claude is called, so these are fast and free.
"""

import pytest
from src.services.reply_classifier import ReplyClassifier


@pytest.fixture
def classifier():
    """Create classifier - API key not needed for heuristic tests."""
    return ReplyClassifier(api_key="test-not-used")


class TestOOODetection:
    @pytest.mark.parametrize("body", [
        "I am currently out of office and will return on January 5th.",
        "Thank you for your email. I am on vacation until next week.",
        "This is an automatic reply. I am currently away from the office.",
        "Auto-reply: I will be out of the office with limited access to email.",
        "I'm currently on leave and will respond when I return.",
    ])
    def test_ooo_detected(self, classifier, body):
        result = classifier.classify(body)
        assert result["category"] == "ooo"
        assert result["input_tokens"] == 0  # heuristic, no API call


class TestBounceDetection:
    @pytest.mark.parametrize("body", [
        "Delivery Status Notification (Failure) - The email account does not exist.",
        "This message was created automatically. Your message to user@example.com is undeliverable.",
        "550 5.1.1 The email account that you tried to reach does not exist.",
        "Mailbox not found. The recipient address rejected your message.",
        "The user's mailbox is full and can't accept messages now.",
    ])
    def test_bounce_detected(self, classifier, body):
        result = classifier.classify(body)
        assert result["category"] == "bounce"


class TestUnsubscribeDetection:
    @pytest.mark.parametrize("body", [
        "Please unsubscribe me from this list.",
        "Remove me from your mailing list immediately.",
        "Stop emailing me.",
        "Please take me off this list.",
        "I'd like to opt out of these emails.",
    ])
    def test_unsubscribe_detected(self, classifier, body):
        result = classifier.classify(body)
        assert result["category"] == "unsubscribe"


class TestEmptyInput:
    def test_empty_string(self, classifier):
        result = classifier.classify("")
        assert result["category"] == "neutral"

    def test_whitespace_only(self, classifier):
        result = classifier.classify("   \n  ")
        assert result["category"] == "neutral"

    def test_none_body(self, classifier):
        result = classifier.classify(None)
        assert result["category"] == "neutral"


class TestNoFalsePositives:
    """Messages that should NOT match any heuristic and would go to Claude."""
    @pytest.mark.parametrize("body", [
        "Thanks for reaching out! I'd love to learn more about this.",
        "Not interested at this time, but thanks.",
        "Can you send me a case study?",
        "Who should I talk to about this on our end?",
    ])
    def test_ambiguous_not_matched_by_heuristic(self, classifier, body):
        """These should NOT be caught by heuristics (they need Claude).
        Since we don't have a real API key, the classifier will fall back to 'neutral'."""
        result = classifier.classify(body)
        # The heuristic should not match these - they're ambiguous
        assert result["category"] == "neutral"
