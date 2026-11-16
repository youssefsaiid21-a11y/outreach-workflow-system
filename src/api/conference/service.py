"""
Conference outreach business logic — AI email generation, draft dispatch, and calendar reminders.

This is the core service layer for conference-based outreach (both post-conference
follow-ups and pre-conference meeting requests). It handles:

  1. **Style-matched email generation**: Loads the user's writing style profile
     (from StyleProfile DB + writing_style markdown) and copy rules, then calls
     Claude to generate personalized emails that match the sender's voice.

  2. **Sequence generation**: Generates multi-step email sequences (email 1, 2, 3...)
     with configurable day offsets, word limits, and follow-up vs. new-angle steps.
     Optionally generates LinkedIn connect note and message text for pre-conference.

  3. **Draft dispatch**: Creates email drafts in the user's Outlook (or IMAP) Drafts
     folder. Follow-up steps are threaded as replies to email 1. Supports CC merging,
     greeting/sign-off stripping (for Outlook signature compatibility), and per-user
     Outlook token resolution.

  4. **Calendar reminders**: When email 1 is drafted for a multi-step sequence,
     schedules Outlook calendar events for each remaining step (email 2 on Day 3,
     email 3 on Day 7, etc.) so the rep gets a native popup at 09:00 local time.
     Calendar events include deep links back to the dashboard sequence modal.

  5. **Validation**: Checks generated emails for banned phrases, word count limits,
     formatting violations (em dashes, semicolons), and template variable replacement.
     Retries Claude up to 2 times if validation fails.

Relationships:
  - Routes in routes_*.py call this service; routes handle auth + persistence only
  - Uses OutlookClient (outlook.py) for draft creation and calendar events
  - Uses draft service (services/draft.py) for the actual Graph/IMAP API calls
  - Uses style_analyzer.py for build_style_prompt()
  - Uses email_gen_helpers.py for _sanitize (prompt injection prevention)
  - Uses signoff.py for sign-off prompt rules
  - Reads sequence config from DB (settings.py) with hardcoded fallback defaults

Security:
  - All lead fields are sanitized via _sanitize_field before prompt injection
  - Per-user StyleProfile scoping prevents cross-rep voice contamination
  - Outlook token files are per-user (tokens/outlook_{user_id}.json)
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ...config import MODEL_SMART
from ...db.models import AppSetting, ConferenceLead, ConferenceInbox, StyleProfile, User
from ...services import draft as draft_svc
from ...services.crypto import decrypt as _decrypt, ENCRYPTION_AVAILABLE
from ...services.style_analyzer import build_style_prompt
from ._shared import (
    get_anthropic_client,
    _outlook_token_file,
    _strip_greeting,
    _strip_signoff,
)
from ...services.signoff import signoff_prompt_rules
from ...services.email_gen_helpers import _sanitize as _sanitize_field
from .prompts import (
    _DEFAULT_SEQ_STEPS,
    _DEFAULT_PRE_SEQ_STEPS,
    _LINKEDIN_CONNECT_NOTE_PROMPT,
    _LINKEDIN_MESSAGE_PROMPT,
    _PRE_CONFERENCE_SYSTEM_PROMPT,
    _SEQ_BANNED,
    _category_instruction,
)

logger = logging.getLogger(__name__)


# ── IMAP password helper ─────────────────────────────────────────────────────


def _get_imap_password(inbox: "ConferenceInbox") -> str:
    """Return the plaintext IMAP password, decrypting if necessary."""
    raw = inbox.imap_password or ""
    if not raw or not ENCRYPTION_AVAILABLE:
        return raw
    try:
        return _decrypt(raw)
    except ValueError:
        return raw


# ── Style block lookup (P3-6 user-scoped) ────────────────────────────────────


def _get_style_block(db: Session, user_id: Optional[str] = None) -> str:
    """
    Return the combined style guide: DB writing_style setting + synced profile.

    Reads from DB on every call so that style updates take effect immediately
    without a server restart.

    P3-6: scoped to ``user_id``. The StyleProfile lookup returns the most
    recent profile owned by that user. If no profile exists for the user,
    the function returns an empty style block  -  it does NOT fall back to
    an unscoped or another user's profile, preventing cross-rep style
    contamination (2026-04-13 Nehal/Vishnu root-cause fix).
    """
    # 1. Writing style markdown - user-scoped with global fallback.
    #    Try __user__{user_id}__writing_style first, then bare writing_style.
    writing_style_md = ""
    if user_id:
        user_row = db.query(AppSetting).filter_by(
            key=f"__user__{user_id}__writing_style"
        ).first()
        if user_row and user_row.value:
            writing_style_md = user_row.value
    if not writing_style_md:
        global_row = db.query(AppSetting).filter_by(key="writing_style").first()
        writing_style_md = global_row.value if global_row and global_row.value else ""

    # 2. Analysed style profile (greeting/sign-off patterns etc.)
    #    STRICT scoping (2026-04-13): when a user_id is provided, ONLY
    #    return that user's profile. Never fall back to NULL-owned profiles
    #    which may contain another rep's voice/signature samples.
    db_style = ""
    try:
        q = db.query(StyleProfile)
        if user_id is not None:
            q = q.filter(StyleProfile.user_id == user_id)
        else:
            # Legacy single-rep path: only use explicitly unowned profiles
            q = q.filter(StyleProfile.user_id.is_(None))
        profile = q.order_by(StyleProfile.synced_at.desc()).first()
        # Belt-and-suspenders: discard if profile's user_id doesn't match
        if profile and user_id is not None and profile.user_id != user_id:
            logger.warning(
                "StyleProfile %s has user_id=%s but expected %s  -  discarding",
                profile.id, profile.user_id, user_id,
            )
            profile = None
        db_style = build_style_prompt(profile.raw_profile) if profile and profile.raw_profile else ""
    except Exception as exc:
        logger.warning("Failed to load StyleProfile: %s", exc)

    parts = [p for p in [writing_style_md, db_style] if p]
    return "\n\n".join(parts)


# ── Sequence config + JSON parsing ───────────────────────────────────────────


def _load_seq_steps(db: Session, user_id: Optional[str] = None, outreach_type: str = "post") -> list[tuple]:
    """Load conference sequence steps from DB config, falling back to defaults.

    Resolution order:
      1. Per-user setting: __user__{uid}__{config_key}
      2. Global setting: {config_key}
      3. Hardcoded defaults (post vs pre)
    """
    from ..settings import _get_user_setting
    config_key = "pre_conference_sequence_config" if outreach_type == "pre" else "conference_sequence_config"
    val = _get_user_setting(db, config_key, user_id)
    if val:
        try:
            data = json.loads(val)
            steps = data.get("steps", [])
            if steps:
                return [
                    (i + 1, s["day"], s["limit"], s["desc"], bool(s.get("is_followup", False)))
                    for i, s in enumerate(steps)
                ]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return _DEFAULT_PRE_SEQ_STEPS if outreach_type == "pre" else _DEFAULT_SEQ_STEPS


def _parse_sequence_json(raw: str) -> dict:
    """Extract a JSON object from Claude's response, stripping markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Stack-based brace extraction
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
                        return json.loads(cleaned[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"Could not parse sequence JSON from Claude response. Preview: {raw[:200]}")


def _validate_sequence(seq: dict, lead: ConferenceLead, seq_steps: list[tuple] | None = None) -> dict:
    """Validate, clean, and annotate the generated sequence."""
    steps = seq_steps or _DEFAULT_SEQ_STEPS
    first_name = lead.first_name or "there"
    warnings: list[str] = []

    for idx, _, limit, _, *_ in steps:
        key = f"email{idx}"
        email = seq.get(key)
        if not email or not isinstance(email, dict):
            continue
        body = email.get("body", "")
        subject = email.get("subject", "")
        if not body or not subject:
            raise ValueError(f"Sequence {key} has empty {'body' if not body else 'subject'}.")

        # Template variable replacement
        body = body.replace("{{first_name}}", first_name)
        body = body.replace("{{company_name}}", lead.company or "your company")
        seq[key]["body"] = body

        # Banned phrase check
        lower_body = body.lower()
        for phrase in _SEQ_BANNED:
            if phrase in lower_body:
                raise ValueError(f"Banned phrase '{phrase}' found in {key}.")

        # Word count warning
        wc = len(body.split())
        if wc > int(limit * 1.2):
            warnings.append(f"{key}: {wc} words (limit {limit})")

        # Formatting violations
        if "\u2014" in body:
            warnings.append(f"{key}: em dash found")
        if ";" in body:
            warnings.append(f"{key}: semicolon found")

    # LinkedIn fields
    for lk_key in ("linkedin_connect_note", "linkedin_message"):
        text = seq.get(lk_key, "")
        if isinstance(text, str) and text.strip():
            text = text.replace("{{first_name}}", first_name)
            seq[lk_key] = text

    seq["_validation"] = {"passed": True, "warnings": warnings}
    return seq


# ── Email generation ─────────────────────────────────────────────────────────


def _build_lead_details_block(lead: ConferenceLead) -> tuple[list[str], str]:
    """Return (lead_details_lines, raw_context_block) for prompt assembly.

    SWAT-H1: all lead fields are sanitized to prevent prompt injection.
    """
    _s = _sanitize_field  # shorthand
    lead_details: list[str] = []
    if lead.category:
        lead_details.append(f"Contact type: {_s(lead.category, 200)}")
    if lead.title:
        lead_details.append(f"Title: {_s(lead.title, 200)}")
    if lead.company:
        lead_details.append(f"Company: {_s(lead.company, 200)}")
    if lead.linkedin_headline:
        lead_details.append(f"LinkedIn headline: {_s(lead.linkedin_headline, 300)}")
    if lead.company_description:
        lead_details.append(f"Company description: {_s(lead.company_description[:300], 300)}")
    if lead.notes:
        lead_details.append(f"Notes from conference: {_s(lead.notes, 500)}")

    # Include full spreadsheet row data so Claude has maximum context
    raw_context = ""
    if lead.raw_data:
        try:
            raw = json.loads(lead.raw_data)
            shown_keys = {
                "email", "first_name", "first name", "firstname",
                "last_name", "last name", "lastname", "company",
                "company name", "title", "job title", "position",
                "notes", "linkedin", "linkedin url", "linkedin_url",
                "website", "company website",
            }
            extra = {k: v for k, v in raw.items() if k.lower() not in shown_keys}
            if extra:
                lines = [f"  {k}: {v}" for k, v in extra.items()]
                raw_context = "Additional info from spreadsheet:\n" + "\n".join(lines)
        except (json.JSONDecodeError, TypeError):
            pass

    return lead_details, raw_context


def _generate_email(
    lead: ConferenceLead,
    context: str,
    db: Session,
    sender_name: str = "",
    user_id: Optional[str] = None,
) -> tuple[str, str, int, int]:
    """Generate a personalised conference outreach email using Claude.

    P3-6: ``user_id`` selects the owning rep's StyleProfile so Rep A's
    generated emails do not inherit Rep B's voice. Falls back to the
    lead's own ``user_id`` when none is supplied.
    """
    _is_pre = getattr(lead, "outreach_type", "post") == "pre"
    style_block = _get_style_block(db, user_id=user_id or getattr(lead, "user_id", None))
    lead_details, raw_context = _build_lead_details_block(lead)

    from ..settings import _get_user_setting, CONFERENCE_COPY_KEY
    _conf_user_id = user_id or getattr(lead, "user_id", None)

    if _is_pre:
        # Pre-conference: uses sales copy principles, no category branching
        conf_copy_rules = _PRE_CONFERENCE_SYSTEM_PROMPT
        cat_instruction = ""
        open_instruction = "- Open by saying what you do in one plain sentence, then reference the event"
    else:
        cat_instruction = _category_instruction(lead.category)
        conf_copy_rules = _get_user_setting(db, CONFERENCE_COPY_KEY, _conf_user_id)
        if not conf_copy_rules or not conf_copy_rules.strip():
            conf_copy_rules = "You write short, warm post-conference outreach emails that feel personal and specific. Tone: friendly and genuine. Confident without being stiff."
        open_instruction = "- Open with a warm, specific reference to meeting at the conference or shared context"

    prompt = f"""{conf_copy_rules.strip()}

{cat_instruction}

Greetings: Hey [Name], Hi [Name], or just [Name],

{signoff_prompt_rules(sender_name)}

{style_block}

Context about the sender and purpose:
{context}

Lead details:
Name: {_sanitize_field(lead.first_name or '', 100)} {_sanitize_field(lead.last_name or '', 100)}
{chr(10).join(lead_details)}
{raw_context}

Use every relevant detail from the lead info above to make the email feel personal and specific. Reference their role, company, interests, sessions attended, or anything else that shows you paid attention.

Write a single outreach email. Additional rules:
{open_instruction}
- Under 100 words total
- One clear CTA, a specific question or next step
- No placeholder text. If you lack context, keep it general but still genuine

Return valid JSON only:
{{"subject": "...", "body": "..."}}"""

    # SWAT-L8: add timeout to prevent indefinite blocks on Claude API
    msg = get_anthropic_client().messages.create(
        model=MODEL_SMART,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
        timeout=60,
    )

    if not msg.content:
        raise ValueError("Claude returned empty response for email generation.")
    raw = msg.content[0].text.strip()
    data = _parse_sequence_json(raw)
    subject = data.get("subject") or ""
    body = data.get("body") or ""
    if not subject or not body:
        raise ValueError(f"Claude returned incomplete email JSON (subject={subject!r}, body={body!r})")
    inp_tokens = msg.usage.input_tokens if msg.usage else 0
    out_tokens = msg.usage.output_tokens if msg.usage else 0
    return subject, body, inp_tokens, out_tokens


def _load_sequence_config(
    lead: ConferenceLead,
    db: Session,
    user_id: Optional[str],
) -> tuple[bool, str, list[str], str, list[tuple], bool, str, str]:
    """Load style, sequence steps, copy rules, and build prompt scaffolding.

    Returns (is_pre, style_block, lead_details, raw_context, seq_steps,
    has_followups, length_section, output_format, conf_copy_rules, cat_instruction).
    """
    _is_pre = getattr(lead, "outreach_type", "post") == "pre"
    effective_uid = user_id or getattr(lead, "user_id", None)
    style_block = _get_style_block(db, user_id=effective_uid)
    lead_details, raw_context = _build_lead_details_block(lead)

    _otype = "pre" if _is_pre else "post"
    seq_steps = _load_seq_steps(db, user_id=effective_uid, outreach_type=_otype)

    has_followups = any(fu for _, _, _, _, fu in seq_steps)
    length_lines = ["Length:"]
    for idx, day, limit, desc, is_followup in seq_steps:
        tag = " [FOLLOW-UP]" if is_followup else ""
        length_lines.append(f"- Email {idx} (Day {day}){tag}: max {limit} words. {desc}")
    length_section = "\n".join(length_lines)

    json_fields = []
    for idx, _, _, _, _ in seq_steps:
        json_fields.append(f'  "email{idx}": {{"subject": "...", "body": "..."}}')
    output_format = "Output format  -  return valid JSON only, no markdown, no extra text:\n{\n" + ",\n".join(json_fields) + "\n}"

    from ..settings import _get_user_setting, CONFERENCE_COPY_KEY

    if _is_pre:
        conf_copy_rules = _PRE_CONFERENCE_SYSTEM_PROMPT + """

Formatting rules:
- No em dashes. Use a comma or rewrite.
- No semicolons. Break into two sentences.
- No bullet points in emails.
- No bold or italics.
- Short paragraphs, 2 to 3 sentences max.
- One blank line between paragraphs.

Words to never use: furthermore, delve, leverage, utilize, in conclusion, it is worth noting, I hope this email finds you well, please do not hesitate, I wanted to reach out, synergy, game-changer, touching base, circle back, just checking in

Email 1: Reference the upcoming event by name. Suggest meeting there. One clear CTA.
Follow-up emails: Simple, direct follow-ups referencing the same thread. 2-3 sentences max.

Self-check:
1. Does email 1 reference the upcoming event and why you want to meet?
2. Is each email a genuinely different angle, not just a rephrased version?
3. Would a real person actually send these? Read them out loud."""
        cat_instruction = ""
    else:
        cat_instruction = _category_instruction(lead.category)
        conf_copy_rules = _get_user_setting(db, CONFERENCE_COPY_KEY, effective_uid)
        if not conf_copy_rules or not conf_copy_rules.strip():
            conf_copy_rules = """You write warm post-conference follow-up email sequences that feel personal and specific.
These are NOT cold emails. You met this person at a conference. The tone is warm, genuine, and conversational.

Tone: like emailing someone you spent 10 minutes talking to at a conference. Friendly, specific, not salesy.

Formatting rules:
- No em dashes. Use a comma or rewrite.
- No semicolons. Break into two sentences.
- No bullet points in emails.
- No bold or italics.
- Short paragraphs, 2 to 3 sentences max.
- One blank line between paragraphs.

Words to never use: furthermore, delve, leverage, utilize, in conclusion, it is worth noting, I hope this email finds you well, please do not hesitate, I wanted to reach out, synergy, game-changer, touching base, circle back, just checking in

Email 1: Warm, specific callback to the conference. One clear CTA.
Follow-up emails: Simple, direct follow-ups referencing the same thread. 2-3 sentences max.

Self-check:
1. Does email 1 reference something specific about the conference or the person?
2. Is each email a genuinely different angle, not just a rephrased version?
3. Would a real person actually send these? Read them out loud."""

    return (_is_pre, style_block, lead_details, raw_context, seq_steps,
            has_followups, length_section, output_format, conf_copy_rules, cat_instruction)


def _build_sequence_prompt(
    lead: ConferenceLead,
    context: str,
    sender_name: str,
    is_pre: bool,
    style_block: str,
    lead_details: list[str],
    raw_context: str,
    has_followups: bool,
    length_section: str,
    output_format: str,
    conf_copy_rules: str,
    cat_instruction: str,
) -> str:
    """Assemble the full Claude prompt string for sequence generation."""
    prompt = f"""{conf_copy_rules.strip()}

{cat_instruction}

Greetings: Hey [Name], Hi [Name], or just [Name],

{signoff_prompt_rules(sender_name)}

{length_section}

{style_block}

Context about the sender and purpose:
{context}

Lead details:
Name: {_sanitize_field(lead.first_name or '', 100)} {_sanitize_field(lead.last_name or '', 100)}
{chr(10).join(lead_details)}
{raw_context}

{"Use every relevant detail above to make each email feel personal. Reference their role, company, and the upcoming event to show you have done your research." if is_pre else "Use every relevant detail above to make each email feel personal. Reference their role, company, interests, sessions, or anything that shows you paid attention at the conference."}

{"Steps marked [FOLLOW-UP] are simple, direct follow-ups to Email 1. They should feel like you are literally following up on your previous email - not introducing a new topic, not sharing a case study, not pitching from a different angle. Keep it short, casual, and to the point. Reference the same CTA from Email 1." if has_followups else ""}

{output_format}"""
    return prompt


def _call_claude_and_validate(
    prompt: str,
    lead: ConferenceLead,
    seq_steps: list[tuple],
) -> tuple[dict, int, int]:
    """Call Claude to generate the sequence JSON, validating with retries.

    Returns (parsed_sequence, total_input_tokens, total_output_tokens).
    """
    msg = get_anthropic_client().messages.create(
        model=MODEL_SMART,
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
        timeout=60,
    )

    if not msg.content:
        raise ValueError("Claude returned empty response for sequence generation.")

    total_inp = msg.usage.input_tokens if msg.usage else 0
    total_out = msg.usage.output_tokens if msg.usage else 0

    raw_text = msg.content[0].text.strip()
    seq = _parse_sequence_json(raw_text)

    max_validation_retries = 2
    for _val_attempt in range(max_validation_retries + 1):
        try:
            seq = _validate_sequence(seq, lead, seq_steps)
            break
        except ValueError as err:
            if _val_attempt >= max_validation_retries:
                raise
            logger.warning(
                "Sequence validation retry %d/%d: %s",
                _val_attempt + 1, max_validation_retries, err,
            )
            correction = (
                f"Your output contained issues: {err}\n\n"
                f"Rewrite the ENTIRE JSON, fixing the violations. "
                f"Do NOT use any of these phrases: {', '.join(_SEQ_BANNED)}.\n"
                f"Return only corrected JSON."
            )
            retry_msg = get_anthropic_client().messages.create(
                model=MODEL_SMART,
                max_tokens=2500,
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": raw_text},
                    {"role": "user", "content": correction},
                ],
                timeout=60,
            )
            if not retry_msg.content:
                raise ValueError("Claude returned empty response on sequence retry.")
            total_inp += retry_msg.usage.input_tokens if retry_msg.usage else 0
            total_out += retry_msg.usage.output_tokens if retry_msg.usage else 0
            raw_text = retry_msg.content[0].text.strip()
            seq = _parse_sequence_json(raw_text)

    return seq, total_inp, total_out


def _generate_linkedin_text(
    lead: ConferenceLead,
    db: Session,
    user_id: Optional[str],
    seq: dict,
) -> tuple[dict, int, int]:
    """Conditionally generate LinkedIn connect note and message text.

    Only generates when the pre-conference config contains LinkedIn steps.
    Returns (updated_seq, extra_input_tokens, extra_output_tokens).
    """
    extra_inp = 0
    extra_out = 0

    _has_linkedin_steps = False
    try:
        from ..settings import _get_user_setting
        _li_conf_user_id = user_id or getattr(lead, "user_id", None)
        _pre_conf_val = _get_user_setting(db, "pre_conference_sequence_config", _li_conf_user_id)
        if _pre_conf_val:
            _pre_conf_data = json.loads(_pre_conf_val)
            for _s in _pre_conf_data.get("steps", []):
                _step_type = (_s.get("type", "") or "").lower()
                _step_preset = (_s.get("preset", "") or "").lower()
                if "linkedin" in _step_type or "linkedin" in _step_preset:
                    _has_linkedin_steps = True
                    break
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    if not _has_linkedin_steps:
        return seq, extra_inp, extra_out

    _first = _sanitize_field(lead.first_name or "there", 100)
    _last = _sanitize_field(lead.last_name or "", 100)
    _title = _sanitize_field(lead.title or "", 200)
    _company = _sanitize_field(lead.company or "", 200)
    _event = lead.conference_name or "the event"

    _li_prompt = (
        _LINKEDIN_CONNECT_NOTE_PROMPT.format(
            first_name=_first, last_name=_last,
            title=_title, company=_company, event_name=_event,
        )
        + "\n\n"
        + _LINKEDIN_MESSAGE_PROMPT.format(
            first_name=_first, event_name=_event,
        )
        + "\n\nReturn valid JSON only:\n"
        '{"linkedin_connect_note": "...", "linkedin_message": "..."}'
    )

    _li_msg = get_anthropic_client().messages.create(
        model=MODEL_SMART,
        max_tokens=300,
        messages=[{"role": "user", "content": _li_prompt}],
        timeout=60,
    )
    if _li_msg.content:
        extra_inp = _li_msg.usage.input_tokens if _li_msg.usage else 0
        extra_out = _li_msg.usage.output_tokens if _li_msg.usage else 0
        try:
            _li_data = _parse_sequence_json(_li_msg.content[0].text.strip())
            seq["linkedin_connect_note"] = _li_data.get("linkedin_connect_note", "")
            seq["linkedin_message"] = _li_data.get("linkedin_message", "")
        except (ValueError, json.JSONDecodeError):
            logger.warning("Failed to parse LinkedIn text from Claude response")

    return seq, extra_inp, extra_out


def _generate_sequence(
    lead: ConferenceLead,
    context: str,
    db: Session,
    sender_name: str = "",
    user_id: Optional[str] = None,
) -> dict:
    """Generate a full follow-up email sequence for a conference lead.

    P3-6: ``user_id`` scopes the StyleProfile lookup to the owning rep
    so sequences never pick up another rep's voice.
    """
    # ── 1. Load config ────────────────────────────────────────────────────────
    (_is_pre, style_block, lead_details, raw_context, seq_steps,
     has_followups, length_section, output_format,
     conf_copy_rules, cat_instruction) = _load_sequence_config(lead, db, user_id)

    # ── 2. Build prompt ───────────────────────────────────────────────────────
    prompt = _build_sequence_prompt(
        lead, context, sender_name, _is_pre,
        style_block, lead_details, raw_context,
        has_followups, length_section, output_format,
        conf_copy_rules, cat_instruction,
    )

    # ── 3. Call Claude & validate ─────────────────────────────────────────────
    seq, total_inp, total_out = _call_claude_and_validate(prompt, lead, seq_steps)

    # ── 4. Conditionally generate LinkedIn text ───────────────────────────────
    if _is_pre:
        seq, li_inp, li_out = _generate_linkedin_text(lead, db, user_id, seq)
        total_inp += li_inp
        total_out += li_out

    # ── 5. Assemble and return ────────────────────────────────────────────────
    seq["_tokens"] = {"input": total_inp, "output": total_out}
    return seq


# ── Draft dispatch ───────────────────────────────────────────────────────────


def _dispatch_draft(
    lead: ConferenceLead,
    inbox: "ConferenceInbox | None",
    current_user: "User | None" = None,
    extra_cc: str | None = None,
    strip_greeting: bool = False,
    signature_html: str = "",
    strip_signoff: bool = True,
) -> dict:
    """Create a draft using the appropriate backend (Outlook or IMAP).

    ``strip_greeting`` removes the opening "Hi John," line (used when the
    Outlook signature block contains a greeting).

    ``strip_signoff`` removes the trailing "Talk soon,\\nName" closer block
    so the Outlook signature can handle sign-off cleanly. Introduced
    2026-04-09 as part of the Nehal/Vishnu leak fix. 2026-04-13: default
    changed to True so the deterministic strip always runs unless
    explicitly disabled.
    """
    to_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email

    # Optionally strip greeting line from body (when Outlook signature has its own)
    body = lead.generated_body or ""
    _sender = getattr(current_user, "name", "") if current_user else ""
    if strip_greeting:
        body = _strip_greeting(body)
    if strip_signoff:
        body = _strip_signoff(body, sender_name="")

    # Merge lead-level CC with global CC
    cc_parts = []
    if lead.cc:
        cc_parts.append(lead.cc)
    if extra_cc:
        cc_parts.append(extra_cc)
    merged_cc = ", ".join(cc_parts) if cc_parts else None

    if inbox is None or inbox.type == "outlook":
        # FIX-28: per-user token cache for the default Outlook pseudo-id.
        if inbox is None or inbox.id == "outlook":
            tf = _outlook_token_file(current_user)
            if not tf.exists():
                raise ValueError("Outlook is not connected for this user. Use Settings → Connect Outlook.")
            token_file = str(tf)
        else:
            tf = Path(__file__).resolve().parent.parent.parent.parent / f"token_cache_{inbox.id}.json"
            token_file = str(tf)
        return draft_svc.create_draft_outlook(
            lead.email, to_name, lead.generated_subject, body,
            token_file=token_file, cc=merged_cc,
            signature_html=signature_html,
        )

    if not inbox.imap_host or not inbox.imap_password:
        raise ValueError("IMAP inbox is missing host or password.")
    return draft_svc.create_draft_imap(
        to_email=lead.email,
        to_name=to_name,
        subject=lead.generated_subject,
        body=body,
        from_email=inbox.email,
        from_name=inbox.name,
        imap_host=inbox.imap_host,
        imap_port=inbox.imap_port or 993,
        imap_password=_get_imap_password(inbox),
        use_ssl=inbox.imap_use_ssl,
    )


def draft_one_step(
    lead: ConferenceLead,
    step_key: str,
    current_user: "User | None",
    *,
    inbox: "ConferenceInbox | None" = None,
    cc: str | None = None,
    strip_greeting: bool = False,
    strip_signoff: bool = True,
    seq_override: "dict | None" = None,
    db: "Session | None" = None,
    signature_html: str = "",
) -> dict:
    """Dispatch a single sequence step (email2, email3, ...) to the rep's
    Outlook/IMAP Drafts folder. Does NOT mutate tracking state  -  the caller
    is responsible for updating ``_drafted_steps`` in whatever manner fits
    their concurrency model.

    This split exists specifically so the auto-draft sweep can do an atomic
    SELECT-UPDATE-COMMIT cycle per step (protecting against concurrent
    writes from the manual route or overlapping ticks), while the manual
    route can stay with the simpler ORM-style mutate-then-commit flow.

    Args:
        lead:           ConferenceLead row (first_name, last_name, email,
                        cc are used for addressing).
        step_key:       ``"email2"``, ``"email3"``, ...
        current_user:   Owner; used to resolve the Outlook token file.
        inbox:          Optional IMAP/Outlook inbox override.
        cc:             Additional CC recipients, merged with lead.cc.
        strip_greeting: Remove opening "Hi X," line.
        strip_signoff:  Remove trailing closer block.
        seq_override:   Optional parsed sequence dict. When the sweep reads
                        the latest sequence JSON via raw SQL, it can pass
                        it here to avoid a second parse from
                        ``lead.generated_sequence`` which may be stale.

    Raises:
        ValueError: sequence missing/corrupt, step missing/empty, or
            credentials unavailable.
        Exception: anything the underlying draft backend raises.

    Returns:
        ``{"message_id": "..."}`` from ``draft_svc.create_draft_*``.
    """
    if seq_override is not None:
        seq = seq_override
    else:
        if not lead.generated_sequence:
            raise ValueError("No sequence generated for this lead.")
        try:
            raw = lead.generated_sequence
            seq = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"Sequence JSON is corrupt: {exc}")

    if not isinstance(seq, dict):
        raise ValueError("Sequence is not a dict.")

    email = seq.get(step_key)
    if not email or not isinstance(email, dict):
        raise ValueError(f"Step '{step_key}' not found in sequence.")

    subject = email.get("subject", "")
    body = email.get("body", "")
    if not subject or not body:
        raise ValueError(f"Step '{step_key}' has empty subject or body.")

    _sender = getattr(current_user, "name", "") if current_user else ""
    if strip_greeting:
        body = _strip_greeting(body)
    if strip_signoff:
        body = _strip_signoff(body, sender_name="")

    to_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email

    cc_parts = []
    if lead.cc:
        cc_parts.append(lead.cc)
    if cc:
        cc_parts.append(cc)
    merged_cc = ", ".join(cc_parts) if cc_parts else None

    # Determine if this step should be created as a reply to email 1.
    # A step is a reply when: (a) the sequence config marks it as a follow-up,
    # (b) the lead has a valid Outlook draft_message_id from email 1, and
    # (c) we're using the Outlook backend (not IMAP).
    _is_reply = False
    if lead.draft_message_id and lead.draft_message_id != "imap-draft" and db is not None:
        try:
            step_num = int(step_key.replace("email", ""))
            for _idx, _day, _lim, _desc, _fu in _load_seq_steps(db, user_id=getattr(lead, "user_id", None), outreach_type=getattr(lead, "outreach_type", "post")):
                if _idx == step_num and _fu:
                    _is_reply = True
                    break
        except (ValueError, TypeError):
            pass

    if inbox is None or inbox.type == "outlook":
        if inbox is None or inbox.id == "outlook":
            tf = _outlook_token_file(current_user)
            if not tf.exists():
                raise ValueError("Outlook is not connected for this user.")
        else:
            tf = Path(__file__).resolve().parent.parent.parent.parent / f"token_cache_{inbox.id}.json"

        if _is_reply:
            # Create as a reply to the original email 1 - same thread.
            # Fall back to a fresh draft if the original message was
            # sent/deleted by the user (Graph returns 400/404).
            try:
                result = draft_svc.create_reply_draft_outlook(
                    lead.draft_message_id, body, token_file=str(tf),
                    to_email=lead.email, to_name=to_name,
                    signature_html=signature_html,
                )
            except Exception as reply_err:
                logger.warning(
                    "Reply draft failed for %s (message may have been sent/deleted), "
                    "falling back to fresh draft: %s", lead.email, reply_err,
                )
                result = draft_svc.create_draft_outlook(
                    lead.email, to_name, subject, body,
                    token_file=str(tf), cc=merged_cc,
                    signature_html=signature_html,
                )
        else:
            result = draft_svc.create_draft_outlook(
                lead.email, to_name, subject, body,
                token_file=str(tf), cc=merged_cc,
                signature_html=signature_html,
            )
    else:
        if not inbox.imap_host or not inbox.imap_password:
            raise ValueError("IMAP inbox is missing host or password.")
        result = draft_svc.create_draft_imap(
            to_email=lead.email, to_name=to_name, subject=subject, body=body,
            from_email=inbox.email, from_name=inbox.name,
            imap_host=inbox.imap_host, imap_port=inbox.imap_port or 993,
            imap_password=_get_imap_password(inbox),
            use_ssl=inbox.imap_use_ssl,
        )
    return result


# ── Calendar reminder helpers (Events-reminders 2026-04-09) ─────────────────
#
# When email 1 of a multi-step sequence is drafted, schedule one Outlook
# calendar event per remaining step so the rep gets a native reminder popup
# when it's time to push email 2, 3, ... The events are timed against
# draft_created_at + the step's day offset from _load_seq_steps, anchored
# to 09:00 in the configured reminder timezone (default Asia/Dubai since
# the current segment focus is GCC; override via CONFERENCE_REMINDER_TIMEZONE).
#
# The IDs are persisted on ConferenceLead.calendar_event_ids as
#     {"email2": "<graph-id>", "email3": "<graph-id>", ...}
# so the reply poller and the per-step draft path can cancel individual
# events when the lead replies (all remaining) or when the rep manually
# drafts a step ahead of its reminder (that one step only).


def _reminder_timezone() -> str:
    """IANA timezone name for follow-up reminders. Env-configurable."""
    import os as _os
    return _os.getenv("CONFERENCE_REMINDER_TIMEZONE", "Asia/Dubai")


def _outlook_client_for_user(current_user: "User | None") -> "object | None":
    """Return an authenticated OutlookClient for this user, or None if
    the user has no Outlook connection. Any failure (expired token,
    missing client id, etc.) returns None and logs  -  calendar scheduling
    must never block draft creation."""
    if current_user is None:
        return None
    try:
        tf = _outlook_token_file(current_user)
    except Exception as exc:
        logger.warning("Calendar: could not resolve token file for user %s: %s",
                       getattr(current_user, "email", "?"), exc)
        return None
    if not tf.exists():
        return None
    try:
        from ...services.outlook import OutlookClient
        client = OutlookClient(token_file=tf)
        client.authenticate()
        return client
    except Exception as exc:
        logger.warning("Calendar: Outlook auth failed for user %s: %s",
                       getattr(current_user, "email", "?"), exc)
        return None


def schedule_followup_reminders(
    db: Session,
    lead: ConferenceLead,
    current_user: "User | None",
    *,
    from_ts: "datetime | None" = None,
) -> None:
    """Schedule Outlook calendar reminders for every sequence step after email 1.

    Called from the draft paths (draft_sequence_step, create_draft, draft_all)
    when email 1 has just been drafted. Best-effort: any error is logged and
    swallowed so the underlying draft operation still succeeds.

    Idempotent-ish: if the lead already has ``calendar_event_ids`` set, the
    existing entries are left alone and we do NOT reschedule  -  this prevents
    the bulk "Push All to Drafts" flow from piling up duplicate reminders
    when it's re-run, and also stops a manual per-step push from clobbering
    reminders already on the calendar. Callers that actually want to rebuild
    (e.g. regenerate sequence) should call ``cancel_followup_reminders`` first.

    The ``from_ts`` argument lets the caller pin the anchor time explicitly
    (useful in tests). When omitted, we use ``lead.draft_created_at`` or now.
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    # Skip if no sequence  -  nothing to remind about.
    if not lead.generated_sequence:
        return

    # Already scheduled once  -  do not pile up duplicates.
    if lead.calendar_event_ids:
        return

    try:
        seq = json.loads(lead.generated_sequence)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(seq, dict):
        return

    try:
        steps = _load_seq_steps(db, user_id=getattr(lead, "user_id", None), outreach_type=getattr(lead, "outreach_type", "post"))
    except Exception as exc:
        logger.warning("Calendar: failed to load seq steps: %s", exc)
        return

    # Anchor: when was email 1 drafted? Fallback to now.
    anchor = from_ts or lead.draft_created_at or _dt.now(_tz.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=_tz.utc)

    # Convert the UTC anchor to the rep's local wall-clock. We schedule
    # reminders for 09:00 *local* on anchor_date + day_offset.
    tz_name = _reminder_timezone()
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = _tz.utc
        tz_name = "UTC"
    anchor_local = anchor.astimezone(local_tz)
    anchor_date = anchor_local.date()

    client = _outlook_client_for_user(current_user)
    if client is None:
        # No Outlook connection at all  -  skip silently.
        return

    scheduled: dict[str, str] = {}
    try:
        # Short-circuit if the cached token lacks Calendars.ReadWrite.
        # The first create_calendar_event call would raise PermissionError
        # anyway, but checking once up front avoids per-step MSAL round-
        # trips and keeps the log clean for the common "user hasn't
        # reconnected yet" case. Any exception from the check itself is
        # also treated as "calendar unavailable" so drafts are never
        # blocked by calendar infrastructure flakes.
        try:
            has_scope = client.has_calendar_scope()
        except Exception as exc:
            logger.warning("Calendar scope check failed: %s", exc)
            has_scope = False
        if not has_scope:
            logger.info(
                "Calendar: user %s has not granted Calendars.ReadWrite yet  -  "
                "follow-up reminders will be skipped until reconnection.",
                getattr(current_user, "email", "?"),
            )
            return

        for step_num, day_offset, _limit, _desc, *_ in steps:
            if step_num <= 1:
                continue  # email 1 is already drafted; no reminder needed
            step_key = f"email{step_num}"
            if step_key not in seq:
                continue  # sequence is shorter than the default (e.g. 3-step)

            # Build a local-naive datetime at 09:00 on the target day.
            target_date = anchor_date + _td(days=int(day_offset))
            start_naive = _dt(
                target_date.year, target_date.month, target_date.day,
                9, 0, 0,
            )

            to_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email
            company = lead.company or ""
            conf_name = lead.conference_name or "conference"
            subject = f"Send email {step_num} to {to_name}"
            if company:
                subject += f" ({company})"
            subject += f"  -  {conf_name}"

            # Deep-link back to the sequence modal. The dashboard already
            # accepts a ?lead= hash in the Events tab; the frontend handler
            # opens the matching row and the modal at the given step.
            import os as _os
            _host = _os.getenv("PUBLIC_HOST", "").strip()
            _port = _os.getenv("PORT", "3004").strip()
            if _host:
                base_url = f"http://{_host}:{_port}"
            else:
                base_url = ""  # No deep-link if PUBLIC_HOST not set
            deep_link = (
                f"{base_url}/#events?lead={lead.id}&step={step_key}"
                if base_url else ""
            )

            preview = (seq[step_key] or {}).get("subject") or ""
            preview_html = (
                f"<p><strong>{to_name}</strong>"
                + (f"  -  {company}" if company else "")
                + "</p>"
                + (f"<p><em>{preview}</em></p>" if preview else "")
                + (f"<p><a href=\"{deep_link}\">Open sequence → {step_key}</a></p>" if deep_link else "")
                + f"<p style=\"color:#888;font-size:12px;\">Auto-scheduled when you drafted email 1 on "
                + anchor_local.strftime("%Y-%m-%d") + f" for conference \"{conf_name}\".</p>"
            )

            try:
                event_id = client.create_calendar_event(
                    subject=subject,
                    body_html=preview_html,
                    start_dt=start_naive,
                    duration_minutes=15,
                    reminder_minutes=60,
                    timezone_name=tz_name,
                )
                scheduled[step_key] = event_id
            except Exception as exc:
                logger.warning(
                    "Calendar: failed to schedule %s for lead %s: %s",
                    step_key, lead.email, exc,
                )
                # Continue with the next step  -  one failure should not
                # cascade and block reminders for the remaining steps.
                continue
    finally:
        try:
            client.close()
        except Exception:
            pass

    if scheduled:
        lead.calendar_event_ids = scheduled
        # Caller is expected to commit  -  we piggy-back on the draft's commit
        # so reminders and draft tracking land atomically.
        logger.info(
            "Calendar: scheduled %d reminder(s) for lead %s (%s)",
            len(scheduled), lead.email, ",".join(scheduled.keys()),
        )


def cancel_followup_reminders(
    lead: ConferenceLead,
    current_user: "User | None",
    *,
    step_keys: "list[str] | None" = None,
) -> None:
    """Cancel Outlook calendar reminders for this lead.

    If ``step_keys`` is None, cancel ALL remaining reminders (used when the
    lead replies or the user clicks "Clear All Drafts"). Otherwise cancel
    only the listed step keys (used when a rep manually drafts email N
    ahead of its reminder, so the stale nudge doesn't still fire).

    Best-effort: any Graph failure is logged and swallowed. The DB field is
    updated in memory  -  the caller is expected to commit.
    """
    if not lead.calendar_event_ids:
        return

    events: dict = dict(lead.calendar_event_ids)  # copy so we can mutate
    target_keys = list(step_keys) if step_keys else list(events.keys())
    if not target_keys:
        return

    client = _outlook_client_for_user(current_user)
    if client is None:
        # Can't reach Graph  -  leave the stored ids alone so a future
        # retry (or admin intervention) can still clean them up.
        return

    try:
        for key in target_keys:
            event_id = events.get(key)
            if not event_id:
                continue
            try:
                client.delete_calendar_event(event_id)
            except Exception as exc:
                logger.warning(
                    "Calendar: failed to cancel %s=%s for lead %s: %s",
                    key, event_id, lead.email, exc,
                )
                continue
            events.pop(key, None)
    finally:
        try:
            client.close()
        except Exception:
            pass

    # Persist whatever still remains (or None if the dict is now empty).
    lead.calendar_event_ids = events or None


def _best_effort_recall_outlook_draft(lead: ConferenceLead, current_user: "User | None") -> None:
    """
    FIX-45: When a lead with an Outlook draft is deleted, attempt to also
    delete the draft from the user's mailbox via Graph. Best-effort only  -
    failures are logged and never propagated.

    Events-reminders 2026-04-09: also cancel any pending follow-up calendar
    reminders so a deleted lead cannot still ping the rep's calendar days
    later.
    """
    # Cancel calendar reminders first  -  this runs even for IMAP drafts,
    # because calendar events live on the rep's Outlook regardless of
    # where the email draft itself was stored.
    if lead.calendar_event_ids:
        try:
            cancel_followup_reminders(lead, current_user)
        except Exception as exc:
            logger.warning(
                "Best-effort calendar cancel failed for lead %s: %s", lead.id, exc,
            )

    if not lead.draft_message_id or lead.draft_message_id == "imap-draft":
        return
    try:
        from ...services.outlook import OutlookClient
        token_path = _outlook_token_file(current_user)
        if not token_path.exists():
            return
        client = OutlookClient(token_file=token_path)
        try:
            access_token = client.authenticate()
            client.http.delete(
                f"https://graph.microsoft.com/v1.0/me/messages/{lead.draft_message_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        finally:
            client.close()
    except Exception as exc:
        logger.warning("Best-effort Outlook draft delete failed for lead %s: %s", lead.id, exc)
