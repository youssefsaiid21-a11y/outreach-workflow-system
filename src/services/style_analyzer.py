"""
Writing style analyzer — extracts voice profiles from a user's sent emails.

Part of the AI email generation pipeline. When a user connects Outlook and
triggers a "style sync", their recent sent emails are fetched (via OutlookClient),
and this module analyzes them to build a style profile that includes:

  - Average word count and sentence length (calibrates generation length)
  - Formality level: conversational / professional / formal (based on marker words)
  - Greeting patterns: "Hey [Name]," vs "Dear [Name]," etc.
  - Sign-off patterns: "Talk soon," vs "Best regards," etc.
  - Bullet point usage frequency
  - 3-5 representative sample emails (20-250 words, sign-offs stripped)

The profile is stored as JSON in the StyleProfile table and injected into every
AI email generation prompt via build_style_prompt(). This ensures Claude generates
emails that match the sender's actual writing voice — not generic AI-sounding copy.

Security:
  Sample email bodies have their sign-off blocks stripped (_sanitize_sample_body)
  before being stored. This prevents Claude from echoing a colleague's name into
  generated emails (the "Nehal/Vishnu incident" — one rep's sign-off leaked into
  another rep's generated outreach because raw samples contained "Talk soon,\\nNehal").

Key functions:
  - analyze_style(emails) — takes raw email dicts, returns a profile dict
  - build_style_prompt(profile) — formats the profile for injection into prompts
  - _sanitize_sample_body(body) — strips sign-off blocks to prevent name leakage
"""

import re
import json
import logging
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

# ── Pattern constants for style detection ───────────────────────────────────
# These patterns are matched against the sender's actual sent emails to
# identify their writing habits. They're intentionally broad — better to
# capture a pattern with some noise than miss it entirely.

# Common greetings in professional email (matched against first ~80 chars)
GREETING_PATTERNS = [
    r"^(hey|hi|hello|dear|good morning|good afternoon)\s+\w+",
    r"^\w+,\s*$",  # "Name," on its own line (terse but common)
]

# Common sign-offs (matched against last ~120 chars of email body)
SIGNOFF_PATTERNS = [
    "talk soon", "cheers", "thanks", "thank you", "best", "regards",
    "looking forward", "speak soon", "warm regards", "sincerely",
    "kind regards", "all the best",
]

# Formality detection: if informal_hits > 1.5x formal_hits → "conversational"
# If formal_hits > 1.5x informal_hits → "formal". Otherwise → "professional"
INFORMAL_MARKERS = ["hey", "yeah", "cool", "awesome", "quick", "chat", "grab"]
FORMAL_MARKERS = ["pursuant", "herewith", "accordingly", "therefore", "regarding", "please find"]


def analyze_style(emails: list[dict]) -> dict:
    """Analyze a collection of sent emails and extract a writing style profile.

    Takes a list of email dicts (with 'subject' and 'body' keys), typically
    from OutlookClient.fetch_sent_emails(). Returns a profile dict that
    captures the sender's voice characteristics for AI email generation.

    The profile is designed to be stored as JSON in StyleProfile.raw_profile
    and later formatted into a prompt block via build_style_prompt().
    """
    if not emails:
        return _empty_profile()

    bodies = [e["body"] for e in emails if e.get("body")]
    if not bodies:
        return _empty_profile()

    word_counts = [_word_count(b) for b in bodies]
    sentence_lengths = [_avg_sentence_length(b) for b in bodies]
    greetings = _extract_greetings(bodies)
    signoffs = _extract_signoffs(bodies)
    formality = _measure_formality(bodies)
    bullet_usage = _measure_bullet_usage(bodies)

    # Pick 5 representative sample emails.
    # Prefer short, standalone messages (20-150 words)  -  those are actual composed emails,
    # not meeting confirmations or one-liners. Sort shortest first to get the most direct ones.
    sorted_emails = sorted(emails, key=lambda e: _word_count(e.get("body", "")))
    samples = []
    for e in sorted_emails:
        body = e.get("body", "")
        wc = _word_count(body)
        if 20 <= wc <= 250:  # ignore one-liners and long threads
            samples.append({
                "subject": e.get("subject", ""),
                "body": body[:600],  # cap for token economy
            })
        if len(samples) >= 5:
            break

    profile = {
        "avg_word_count": round(sum(word_counts) / len(word_counts), 1) if word_counts else 0,
        "avg_sentence_length": round(
            sum(s for s in sentence_lengths if s > 0) / max(len([s for s in sentence_lengths if s > 0]), 1), 1
        ),
        "formality": formality,
        "greeting_patterns": greetings[:5],
        "sign_off_patterns": signoffs[:5],
        "uses_bullet_points": bullet_usage["uses_bullets"],
        "bullet_point_frequency": round(bullet_usage["frequency"], 2),
        "sample_emails": samples,
        "total_emails_analyzed": len(bodies),
    }

    logger.info(
        f"Style analysis complete: {len(bodies)} emails, "
        f"avg {profile['avg_word_count']} words, formality={formality}"
    )
    return profile


# Events-sig-fix 2026-04-09: any sample-email line that matches this pattern
# is truncated at the match. Style-sync pulls raw sent-folder emails, which
# routinely contain a colleague's name in the trailing sign-off. When those
# raw samples were injected verbatim into the generation prompt, Claude would
# echo the colleague's name into new outreach emails (Nehal/Vishnu incident).
# The sanitiser below strips everything from the first sign-off word onwards
# so the examples preserve their tone/voice/length signal but never leak a
# person-name.
_SAMPLE_SIGNOFF_RE = re.compile(
    r"(?is)(?:^|\n|[.!?]\s+)"
    r"(thanks(?:\s+and\s+regards)?|thank\s+you|regards|best(?:\s+regards)?|"
    r"cheers|talk\s+soon|speak\s+soon|looking\s+forward|sincerely|warmly|"
    r"warm\s+regards|kind\s+regards|all\s+the\s+best)\b.*$"
)


def _sanitize_sample_body(body: str) -> str:
    """
    Strip the trailing sign-off (and any name that follows) from a sample
    email body before it's embedded in the generation prompt.

    Returns the sample with the sign-off cut off, or the original body if no
    recognised closer is found. Always trims trailing whitespace.
    """
    if not body:
        return body
    cleaned = _SAMPLE_SIGNOFF_RE.sub("", body).rstrip()
    # If the whole body was a sign-off (rare but possible for one-line
    # confirmations), fall back to an empty string rather than leaking it.
    return cleaned if cleaned else ""


def build_style_prompt(profile: Optional[dict]) -> str:
    """
    Returns the style context block that gets injected into the email generation prompt.
    Returns empty string if profile is None.
    """
    if not profile:
        return ""
    samples_text = ""
    for i, s in enumerate(profile.get("sample_emails", [])[:3], 1):
        sanitized_body = _sanitize_sample_body(s.get("body", ""))
        # Skip samples that were ENTIRELY a sign-off  -  they have no style
        # signal left after sanitising, and including an empty body in the
        # prompt wastes tokens.
        if not sanitized_body.strip():
            continue
        samples_text += f"\nExample {i}:\nSubject: {s.get('subject', '')}\n{sanitized_body}\n---"

    greetings = ", ".join(profile.get("greeting_patterns", ["Hi {{first_name}},"]))
    signoffs = ", ".join(profile.get("sign_off_patterns", ["Talk soon,"]))

    return f"""Writing style profile:
- Average email length: {profile.get('avg_word_count', 80)} words
- Average sentence length: {profile.get('avg_sentence_length', 12)} words
- Tone: {profile.get('formality', 'conversational')}
- Typical greetings used: {greetings}
- Typical sign-offs used: {signoffs}
- Uses bullet points: {'yes, occasionally' if profile.get('uses_bullet_points') else 'rarely or never'}

Real email examples from this sender (sign-off blocks removed to prevent name leakage  -  the actual sender's signature is handled by Outlook):
{samples_text}"""


def _word_count(text: str) -> int:
    return len(text.split())


def _avg_sentence_length(text: str) -> float:
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if not sentences:
        return 0.0
    word_counts = [len(s.split()) for s in sentences]
    return sum(word_counts) / len(word_counts)


def _extract_greetings(bodies: list[str]) -> list[str]:
    found = Counter()
    for body in bodies:
        # After HTML stripping, all whitespace is collapsed to spaces  -  no newlines.
        # Use the first 80 characters to find the greeting, not split("\n")[0].
        head = body.strip()[:80]
        for pattern in GREETING_PATTERNS:
            m = re.match(pattern, head, re.IGNORECASE)
            if m:
                # Extract just the matched portion (the greeting + name), then normalise.
                matched_text = m.group(0).strip().rstrip(",")
                # Replace the actual name with a placeholder
                greeting = re.sub(r"\s+\S+$", " [Name],", matched_text.title())
                found[greeting] += 1
                break
    return [g for g, _ in found.most_common(5)]


def _extract_signoffs(bodies: list[str]) -> list[str]:
    found = Counter()
    for body in bodies:
        # After HTML stripping, bodies are single-line  -  search the last 120 chars.
        tail = body.strip()[-120:].lower()
        for pattern in SIGNOFF_PATTERNS:
            if pattern in tail:
                found[pattern.title()] += 1
    return [s for s, _ in found.most_common(5)]


def _measure_formality(bodies: list[str]) -> str:
    informal_hits = 0
    formal_hits = 0
    for body in bodies:
        lower = body.lower()
        informal_hits += sum(1 for w in INFORMAL_MARKERS if w in lower)
        formal_hits += sum(1 for w in FORMAL_MARKERS if w in lower)

    if informal_hits > formal_hits * 1.5:
        return "conversational"
    elif formal_hits > informal_hits * 1.5:
        return "formal"
    return "professional"


def _measure_bullet_usage(bodies: list[str]) -> dict:
    # Bodies are single-line after HTML stripping, so look for inline bullet markers.
    bullet_bodies = sum(
        1 for b in bodies
        if re.search(r"\s[-*•]\s+\w", b)
    )
    frequency = bullet_bodies / len(bodies) if bodies else 0
    return {
        "uses_bullets": frequency > 0.1,
        "frequency": frequency,
    }


def _empty_profile() -> dict:
    return {
        "avg_word_count": 80,
        "avg_sentence_length": 12,
        "formality": "conversational",
        "greeting_patterns": ["Hi [Name],"],
        "sign_off_patterns": ["Talk soon,"],
        "uses_bullet_points": False,
        "bullet_point_frequency": 0.0,
        "sample_emails": [],
        "total_emails_analyzed": 0,
    }
