"""
Reply classification service.

Uses Claude Haiku to classify email replies into actionable categories so the
orchestration layer can route them to the correct terminal state and surface
manual tasks when needed.

Categories:
  positive     -  interested, wants meeting, asks for details
  negative     -  not interested, wrong person, bad timing
  ooo          -  out of office / auto-reply
  unsubscribe  -  wants to be removed
  bounce       -  delivery failure notification
  neutral      -  unclear intent, needs human review
"""

import logging
import os
from typing import Optional

import anthropic

from ..config import MODEL_FAST

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """You classify cold outreach email replies into exactly one category.

Categories:
- positive: prospect shows interest, asks a question, wants to meet, requests more info
- negative: not interested, wrong person, bad timing, do not contact
- ooo: out of office, vacation auto-reply, will return on date
- unsubscribe: explicitly asks to be removed or stop emails
- bounce: delivery failure, mailbox full, address not found
- neutral: unclear intent, forwarded to someone, generic acknowledgement

Reply with ONLY the category name, nothing else. One word."""


class ReplyClassifier:
    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )

    def classify(self, reply_body: str, original_subject: str = "") -> dict:
        """
        Classify a reply email.

        Returns:
            {
                "category": "positive"|"negative"|"ooo"|"unsubscribe"|"bounce"|"neutral",
                "input_tokens": int,
                "output_tokens": int,
            }
        """
        if not reply_body or not reply_body.strip():
            return {"category": "neutral", "input_tokens": 0, "output_tokens": 0}

        # Strip common email footers/signatures that contain "unsubscribe" links
        # to avoid false positives from forwarded original email footers
        import re
        cleaned = re.split(
            r"(?i)(?:^|\n)[-_]{3,}|(?:^|\n)(?:From:|Sent:|On .+ wrote:)|"
            r"(?:^|\n)(?:Disclaimer|Confidentiality Notice)|"
            r"(?:^|\n)(?:To unsubscribe|Click here to unsubscribe|Manage your preferences)",
            reply_body, maxsplit=1
        )[0]

        # Quick heuristic checks before burning API tokens
        lower = cleaned.lower() if cleaned.strip() else reply_body.lower()

        # Auto-reply / OOO detection (deterministic  -  no API call needed)
        ooo_signals = [
            "out of office", "out of the office", "currently away",
            "on vacation", "on leave", "will return", "auto-reply",
            "automatic reply", "autoreply", "i am currently out",
        ]
        if any(sig in lower for sig in ooo_signals):
            logger.info("Reply classified as OOO via heuristic")
            return {"category": "ooo", "input_tokens": 0, "output_tokens": 0}

        # Bounce detection (deterministic)
        bounce_signals = [
            "delivery status notification", "undeliverable",
            "mailbox not found", "user unknown", "address rejected",
            "550 ", "553 ", "mailbox full", "mailbox is full",
            "over quota", "can't accept messages",
        ]
        if any(sig in lower for sig in bounce_signals):
            logger.info("Reply classified as bounce via heuristic")
            return {"category": "bounce", "input_tokens": 0, "output_tokens": 0}

        # Unsubscribe detection (deterministic)
        unsub_signals = [
            "unsubscribe", "remove me", "stop emailing",
            "take me off", "opt out", "do not contact",
            "don't email", "dont email", "stop contacting",
        ]
        if any(sig in lower for sig in unsub_signals):
            logger.info("Reply classified as unsubscribe via heuristic")
            return {"category": "unsubscribe", "input_tokens": 0, "output_tokens": 0}

        # Ambiguous  -  use Claude Haiku for classification
        user_msg = f"Original subject: {original_subject}\n\nReply:\n{cleaned[:1500]}"

        try:
            message = self.client.messages.create(
                model=MODEL_FAST,
                max_tokens=10,
                system=CLASSIFY_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = message.content[0].text.strip().lower()
            valid = {"positive", "negative", "ooo", "unsubscribe", "bounce", "neutral"}
            category = raw if raw in valid else "neutral"

            logger.info("Reply classified as '%s' via Claude", category)
            return {
                "category": category,
                "input_tokens": message.usage.input_tokens if message.usage else 0,
                "output_tokens": message.usage.output_tokens if message.usage else 0,
            }
        except Exception as e:
            logger.error("Reply classification failed, defaulting to neutral: %s", e)
            return {"category": "neutral", "input_tokens": 0, "output_tokens": 0}

    def classify_batch(self, replies: list[dict]) -> list[dict]:
        """
        Classify multiple replies.

        Args:
            replies: list of {"reply_body": str, "original_subject": str, ...}

        Returns:
            Same list with "classification" key added to each dict.
        """
        results = []
        for reply in replies:
            result = self.classify(
                reply.get("reply_body", ""),
                reply.get("original_subject", ""),
            )
            results.append({**reply, "classification": result})
        return results
