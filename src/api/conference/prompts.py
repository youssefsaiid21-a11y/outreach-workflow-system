"""
Prompt fragments and constants for the conference email/sequence generators.

P4-1: extracted from the old monolith. Keeping these as plain strings
plus tiny pure-function builders means the prompts can be unit-tested
without importing the full Anthropic client and DB session.
"""
from __future__ import annotations


# Default 3-step Day-0/3/7 sequence used when no DB override is set.
# Each tuple: (step_index, day_offset, word_limit, description, is_followup).
# The DB config (AppSetting key="conference_sequence_config") is the real
# source of truth  -  this fallback only fires when no config row exists.
_DEFAULT_SEQ_STEPS: list[tuple[int, int, int, str, bool]] = [
    (1, 0,  100, "Warm follow-up referencing the conference. Specific detail about what you discussed or their work. One clear CTA.", False),
    (2, 3,  70,  "Value add. Share a relevant insight, article idea, or case study that connects to their world. Light nudge toward a call.", True),
    (3, 7,  60,  "Social proof. Mention a specific result or customer story relevant to their industry. Keep it conversational.", True),
]


# Phrases the validator rejects on output. Anything found here triggers
# a regenerate-and-retry loop in ``service._generate_sequence``.
_SEQ_BANNED: list[str] = [
    "furthermore", "delve", "leverage", "utilize", "in conclusion",
    "it is worth noting", "i hope this email finds you well",
    "please do not hesitate", "i wanted to reach out",
    "i am writing to", "synergy", "game-changer", "touching base",
    "circle back", "just checking in",
    "worth",
]


# Per-category tone instruction. Falls back to a generic line for any
# category that isn't in the map.
_CATEGORY_INSTRUCTIONS: dict[str, str] = {
    "partner":  "This person is a PARTNER  -  tone should be collaborative, peer-to-peer. Focus on mutual value and working together.",
    "referral": "This person was REFERRED to you  -  mention the referral naturally. Tone is warm but slightly more formal than someone you met directly.",
    "lead":     "This is a prospective LEAD  -  tone is warm but purposeful. Guide toward a meeting or next step.",
}


def _category_instruction(category: str | None) -> str:
    """Return the tone instruction for a category, or '' if no category."""
    if not category:
        return ""
    return _CATEGORY_INSTRUCTIONS.get(category.lower(), f"Contact type: {category}")


# ── Pre-Conference Outreach ──────────────────────────────────────────────────
# Default 3-step Day-0/3/7 sequence for cold outreach before an event.
# Same tuple format as _DEFAULT_SEQ_STEPS.
_DEFAULT_PRE_SEQ_STEPS: list[tuple[int, int, int, str, bool]] = [
    (1, 0,  100, "Cold-warm introduction referencing the upcoming conference/event. Mention you will be attending and suggest meeting there. One clear CTA.", False),
    (2, 3,  70,  "Follow-up with a value add relevant to their work and the event. Share a brief insight or talking point. Light nudge toward scheduling a meeting at the event.", True),
    (3, 7,  60,  "Final nudge. Keep it brief and conversational. Reiterate interest in connecting at the event. Light social proof if available.", True),
]

# System-prompt fragment injected when outreach_type == "pre".
_PRE_CONFERENCE_SYSTEM_PROMPT = """\
You are writing a SHORT cold-warm outreach email to someone who will be \
attending an upcoming conference or event. You have NOT met this person yet \
but you will both be at the same event soon.

SUBJECT LINE FORMAT:
- Structure: [Their Company] - AI for Safety - Example AI
- Example: Saudi Aramco - AI for Safety - Example AI
- Keep it short, professional, no clickbait, no "Quick Connect" or "Let's Chat"

SALES COPY RULES (follow these strictly):
- Sell the problem, not the product. Features are just proof.
- Opening: Say what we do in one plain sentence. Optionally mention an active \
  project or deployment. That's it. No abstract pain statements.
- Strong opening example: "We do AI video analytics for safety on high-risk \
  worksites, currently deployed on an HDB project in Singapore."
- Bad opening: "Managing safety across large sites is tough when hazards only \
  surface after something goes wrong."
- Pitch: What we do + who uses it + a measurable result (e.g. "reduced incidents \
  by 40%", "cut manual inspection time by 37%").
- CTA: Ask within 3-5 lines. Make it easy to say yes to. Reference the event \
  (e.g. "grab 15 minutes at the event", "meet for coffee on Day 1").
- 60-100 words maximum. 3-5 sentences. One idea per sentence.
- The best email is the one you cannot remove any word from.
- Every message must reference at least one specific context signal: their \
  company, industry, role, or a likely operational challenge.
- No marketing buzzwords. No filler. No "I hope this finds you well."
- Frame around preventing incidents before they happen, not around AI technology.
- Tone: professional but human. One professional reaching out to another.
"""


# ── LinkedIn text generation prompts ─────────────────────────────────────────

_LINKEDIN_CONNECT_NOTE_PROMPT = """Write a LinkedIn connection request note (max 30 words) for {first_name} {last_name}, {title} at {company}.
You are both attending {event_name}. Say what you do in one sentence (AI video analytics for safety on high-risk worksites). Mention the event. No buzzwords. No "I'd love to connect" — be direct."""

_LINKEDIN_MESSAGE_PROMPT = """Write a LinkedIn message (max 50 words) to {first_name} who accepted your connection request.
You are both attending {event_name}. Thank them briefly, say what you do (AI safety analytics for worksites), mention a result (e.g. reduced incidents by 40%), suggest meeting at the event. One clear CTA. No filler."""
