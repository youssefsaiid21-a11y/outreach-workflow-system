"""
Deterministic email sign-off / signature stripping.

History:
- Originally lived inside ``src/api/conference/_shared.py`` because only the
  conference (events) draft path needed it.
- 2026-04-09 (Nehal/Vishnu + Nasser v1.1 incidents): the leak was reproducing
  on the main email generation path, which never had any deterministic
  strip  -  only prompt instructions, which are probabilistic. We extracted the
  helper here so both ``email_gen.generate_sequence`` and the conference
  drafts can rely on the same logic.

Behaviour:
- ``strip_signoff`` walks an email body from the bottom up and removes a
  trailing closer block (e.g. ``Talk soon,\\nVishnu`` or
  ``Thanks and Regards,\\nVishnu Saran``) so the recipient client (Outlook) appends the actual sender's signature without leaking a
  colleague name from style-sync samples.
- The function is intentionally conservative: at most 2 short trailing name
  lines are popped, and we only commit the strip if a recognised closer word
  ("Regards", "Talk soon", "Cheers", ...) was found. If no closer line is
  identified, the body is returned unchanged so legitimate short trailing
  sentences are never accidentally truncated.
"""
from __future__ import annotations

import re

# Sign-off (closer) patterns  -  the last few words of a typical email signature.
_SIGNOFF_WORDS = (
    r"thanks(?:\s+and\s+regards)?|thank\s+you|regards|best(?:\s+regards)?|"
    r"cheers|talk\s+soon|speak\s+soon|looking\s+forward|sincerely|warmly|"
    r"warm\s+regards|kind\s+regards|all\s+the\s+best"
)
SIGNOFF_LINE_RE = re.compile(
    rf"^\s*(?:{_SIGNOFF_WORDS})\b[^\n]{{0,80}}[,.!]?\s*$",
    re.IGNORECASE,
)


def _looks_like_name_line(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    words = s.split()
    if len(words) > 4:
        return False
    # Reject lines with sentence punctuation  -  those are content, not names.
    if any(ch in s for ch in ".?!"):
        return False
    # Must be mostly alphabetic words.
    return all(w.replace("-", "").replace("'", "").isalpha() for w in words)


def strip_signoff(body: str, sender_name: str = "") -> str:
    """Strip a trailing closer + name block then re-append with the correct
    sender name.

    When ``sender_name`` is provided the function guarantees the email ends
    with ``<closer>,\\n<sender_name>`` using the original closer word that
    Claude generated but replacing whatever name was there (which may have
    leaked from style-sync samples) with the authenticated sender.

    When ``sender_name`` is empty the behaviour is pure-strip (legacy mode)
    for backwards-compat with call sites that don't have sender context.

    Returns the body unchanged if no recognised closer is found.
    """
    if not body:
        return body
    lines = body.rstrip().split("\n")
    if not lines:
        return body

    # Phase 1: pop trailing empty lines.
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return body

    # Phase 2: pop up to 2 short trailing name lines.
    popped_name_lines = 0
    while lines and popped_name_lines < 2 and _looks_like_name_line(lines[-1]):
        lines.pop()
        popped_name_lines += 1
        # Drop one blank line between name and closer if present.
        while lines and not lines[-1].strip():
            lines.pop()

    # Phase 3: look for the actual closer line.
    if not lines:
        return ""
    if SIGNOFF_LINE_RE.match(lines[-1].strip()):
        closer_line = lines.pop().strip()
        while lines and not lines[-1].strip():
            lines.pop()
        result = "\n".join(lines).rstrip() + "\n"
        # Re-append closer + correct sender name
        if sender_name:
            result += f"\n{closer_line}\n{sender_name}"
        return result

    # No closer line found  -  popping name lines may have been wrong. Restore.
    if popped_name_lines > 0:
        return body
    return body


# Hard prompt block reused on every code path that asks Claude to write an
# email body. Keeps the rules in one place so a fix here propagates to both
# ``email_gen.py`` and the conference draft prompts.
def signoff_prompt_rules(sender_name: str = "") -> str:
    """Build the sign-off prompt block, injecting the correct sender name.

    When ``sender_name`` is provided Claude is told to sign with that exact
    name. A deterministic strip-and-replace still runs at draft time as a
    safety net, but giving Claude the right name up-front produces better
    first-pass output.
    """
    if sender_name:
        return (
            f"Sign-off rules:\n"
            f"- End every email with a short closer line. Alternate between "
            f"\"Best,\" / \"Best regards,\" / \"Talk soon,\" across the sequence. "
            f"Follow the closer with a newline, then the "
            f"sender's name: {sender_name}\n"
            f"- ONLY use \"{sender_name}\" as the name. If the style examples "
            f"below show a different name after the closer, IGNORE it. Never "
            f"copy names from examples.\n"
            f"- No title, no contact info, no signature block. Just closer + name."
        )
    # Fallback when no sender_name is available (legacy callers)
    return (
        "Sign-off rules:\n"
        "- End with EXACTLY one closer line (e.g. \"Talk soon,\") followed by "
        "a newline, then the sender's first name.\n"
        "- If the style examples below show a name after the closer, IGNORE it."
    )


# Legacy constant for any code that still references it directly.
SIGNOFF_PROMPT_RULES = signoff_prompt_rules()
