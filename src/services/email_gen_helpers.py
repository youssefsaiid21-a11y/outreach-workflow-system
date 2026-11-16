"""
Shared helpers for AI email generation — sanitization, JSON parsing, and prompt building.

Extracted from email_gen.py so they can be imported and unit-tested without
pulling in the Anthropic client or the case-study cache at import time. Used
by both the main campaign email generation (email_gen.py) and the conference
outreach generation (conference/service.py).

Key functions:

  _sanitize(text, max_len)
    Prompt-injection defense: truncates user-supplied text and strips known
    injection patterns ("ignore previous instructions", "you are now", etc.).
    Applied to every lead field (name, title, company, notes) before they're
    injected into Claude prompts. Returns empty string if injection detected.

  _parse_json_response(raw)
    Robust JSON extraction from LLM output. Claude sometimes wraps JSON in
    markdown code fences, adds leading prose ("Here's the sequence:"), or
    includes trailing commentary. This function handles all of those via:
    1. Strip markdown fences → try direct parse (fast path)
    2. Stack-based brace extraction that correctly handles braces inside
       JSON string values (e.g. {"body": "test {thing}"})

  _word_limits_for_steps(n)
    Dynamic per-step word budgets: Email 1 = 100 words (needs more context),
    emails 2-4 = 60 words, emails 5+ = 50 words. Replaces a former hardcoded
    6-element list that silently truncated longer sequences.

  _build_dynamic_system_prompt(template, seq_config)
    Injects the correct number of email steps + LinkedIn steps into the system
    prompt template based on the SequenceConfig. Handles variable-length
    sequences (3-step, 6-step, 8-step, etc.) and optional LinkedIn steps.

``email_gen.py`` re-exports these names so existing callers keep working.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ── Prompt-injection sanitiser ──────────────────────────────────────────

_INJECTION_PATTERN = re.compile(
    r"(ignore (all |previous |above |prior |the )?instructions?|"
    r"new instructions?|disregard|you are now|act as|pretend (you are|to be)|"
    r"system prompt|reveal (your|the) (prompt|instructions)|"
    r"forget (everything|all))",
    re.IGNORECASE,
)


def _sanitize(text: str, max_len: int) -> str:
    """Truncate and strip obvious prompt-injection attempts from user text."""
    if not text:
        return ""
    text = text[:max_len]
    if _INJECTION_PATTERN.search(text):
        logger.warning(
            "Possible prompt injection detected in user-supplied field  -  field cleared."
        )
        return ""
    return text


# ── Robust JSON extractor ───────────────────────────────────────────────


def _parse_json_response(raw: str) -> dict:
    """
    Parse an LLM JSON response, tolerating common noise.

    Handles:
    - Markdown code fences (``` or ```json).
    - Leading / trailing prose around a JSON object.
    - Braces inside string values (stack-based extraction).

    Raises:
        ValueError: if no parseable JSON object can be found.
    """
    # 1. Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # 2. Direct parse  -  fast path for well-formed responses
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Stack-based brace extraction  -  handles leading prose or trailing text.
    #    Also correctly ignores braces inside JSON string values.
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(cleaned[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break

    logger.error(
        "Failed to parse email generation response  -  model did not return "
        "valid JSON. First 400 chars of raw output: %s",
        raw[:400],
    )
    raise ValueError(
        f"Claude returned output that could not be parsed as JSON. Preview: {raw[:200]}"
    )


# Alias requested by the P4-4 task description (mirrors the name used in
# conference.py so both modules can import the same helper).
_parse_sequence_json = _parse_json_response


# ── Dynamic word limits for N-step sequences ───────────────────────────


def _word_limits_for_steps(n: int) -> list[int]:
    """Return per-step word limits for an N-step sequence.

    Step 1 anchors at 100 words; subsequent steps drop to 60, then 50
    once the sequence runs longer than the canonical 6-step format.
    Used to be a hardcoded 6-element list  -  sequences with >6 emails
    were silently truncated, leading to missing word-limit instructions
    for steps 7+.
    """
    if n <= 0:
        return []
    limits: list[int] = []
    for i in range(n):
        if i == 0:
            limits.append(100)
        elif i < 4:
            limits.append(60)
        else:
            limits.append(50)
    return limits


# ── Default copy for system prompt ─────────────────────────────────────

_DEFAULT_EMAIL_DESCRIPTIONS = [
    "Problem intro, one low-commitment CTA question.",
    "Follow-up insight, references the first email naturally.",
    "Soft case study reference, short and direct.",
    "Pattern break, take a different angle or format.",
    "Light nudge, keep it casual.",
    "Break-up email, signal you will stop emailing.",
]


def _build_dynamic_system_prompt(system_prompt_template: str, seq_config=None) -> str:
    """Build the system prompt with email steps derived from the sequence config.

    This is the same logic as the private helper inside ``email_gen.py`` but
    takes the raw ``SYSTEM_PROMPT`` template as an argument so the helpers
    module does not need to import the template itself (and so tests can
    substitute a minimal template).
    """
    email_steps: list[tuple[int, int, int, str]] = []
    has_linkedin_connect = False
    has_linkedin_message = False

    if seq_config and hasattr(seq_config, "steps"):
        email_total = sum(
            1
            for s in seq_config.steps
            if (
                s.step_type.value if hasattr(s.step_type, "value") else str(s.step_type)
            )
            == "send_email"
        )
        word_limits = _word_limits_for_steps(email_total)
        email_idx = 0
        for step in seq_config.steps:
            st = (
                step.step_type.value
                if hasattr(step.step_type, "value")
                else str(step.step_type)
            )
            if st == "send_email":
                email_idx += 1
                label = (
                    getattr(step, "label", "")
                    or _DEFAULT_EMAIL_DESCRIPTIONS[min(email_idx - 1, 5)]
                )
                word_limit = word_limits[email_idx - 1]
                day = step.day_offset
                email_steps.append((email_idx, day, word_limit, label))
            elif st == "send_linkedin_connect":
                has_linkedin_connect = True
            elif st == "send_linkedin_message":
                has_linkedin_message = True
    else:
        email_steps = [
            (1, 0, 100, "Problem intro, one low-commitment CTA question."),
            (2, 3, 60, "Follow-up insight, references the first email naturally."),
            (3, 7, 60, "Soft case study reference, short and direct."),
            (4, 14, 60, "Pattern break, take a different angle or format."),
            (5, 21, 50, "Light nudge, keep it casual."),
            (6, 28, 50, "Break-up email, signal you will stop emailing."),
        ]
        has_linkedin_connect = True
        has_linkedin_message = True

    lines = ["Length:"]
    for idx, day, limit, desc in email_steps:
        lines.append(f"- Email {idx} (Day {day}): max {limit} words. {desc}")
    if has_linkedin_connect:
        lines.append(
            "- linkedin_connect_note: max 30 words. Short personalized connection "
            "request note. No pitch."
        )
    if has_linkedin_message:
        lines.append(
            "- linkedin_message: max 50 words. Follow-up message after they accept "
            "the connection. Reference a shared interest or their work."
        )

    steps_section = "\n".join(lines)

    json_fields: list[str] = []
    for idx, _, _, _ in email_steps:
        json_fields.append(f'  "email{idx}": {{"subject": "...", "body": "..."}}')
    if has_linkedin_connect:
        json_fields.append('  "linkedin_connect_note": "..."')
    if has_linkedin_message:
        json_fields.append('  "linkedin_message": "..."')

    output_section = (
        "Output format, return valid JSON only, no markdown, no extra text, "
        "no preamble:\n{\n" + ",\n".join(json_fields) + "\n}"
    )

    return system_prompt_template.replace(
        "{{SEQUENCE_STEPS_SECTION}}", steps_section
    ).replace("{{OUTPUT_FORMAT_SECTION}}", output_section)
