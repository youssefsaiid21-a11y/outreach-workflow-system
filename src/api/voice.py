"""
Voice command interpreter.
Transcripts from the browser's Web Speech API are sent here.
Claude parses intent and returns a structured action the frontend executes.
Uses Haiku for minimal latency.
"""
import os
import json
import logging
import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..config import MODEL_FAST
from .auth import require_api_key


def _voice_keyfunc(request: Request) -> str:
    """Per-user rate limit key. Falls back to the client IP if no API key
    header is present (the router-level auth dep already rejected anonymous
    callers, so this is just a safety net)."""
    key = request.headers.get("x-api-key") or request.headers.get("authorization") or ""
    return key or get_remote_address(request)


_voice_limiter = Limiter(key_func=_voice_keyfunc)

# Defense-in-depth: declare auth at the router constructor so the endpoints
# cannot be mounted without authentication even if a future include_router
# call forgets to pass `dependencies=_auth`. See deep audit CRITICAL-5.
router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    dependencies=[Depends(require_api_key)],
)
logger = logging.getLogger(__name__)

# Module-level client  -  reuses TCP connections across requests.
_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM = """You are a command interpreter for an outbound sales platform.
Parse the user's voice command and return ONE JSON action object. No markdown. No explanation.

Available actions:

navigate      -  go to a section
{"action":"navigate","section":"leads|email|analytics|conference"}

search_leads  -  search Apollo for new leads
{"action":"search_leads","titles":["..."],"locations":["..."],"industries":["..."],"size":"2000,10000","limit":20}

filter_leads  -  filter the visible leads table by text
{"action":"filter_leads","query":"..."}

select_campaign  -  select a campaign in the email workflow
{"action":"select_campaign","name":"..."}

edit_email    -  rewrite a specific email given a style instruction
{"action":"edit_email","email_num":1,"instruction":"...","subject":"rewritten subject","body":"rewritten body"}
Only include subject/body if you can confidently rewrite them from the instruction + email context provided.

generate_emails  -  start email generation for the current campaign
{"action":"generate_emails"}

speak         -  answer a question or confirm an action verbally
{"action":"speak","text":"..."}

Return ONLY the JSON object."""


class VoiceRequest(BaseModel):
    transcript: str
    context: dict = {}   # optional: {section, campaign_name, email_body, email_subject, email_num}


@router.post("/command")
@_voice_limiter.limit("30/minute")
def voice_command(request: Request, req: VoiceRequest):
    context_str = ""
    if req.context:
        context_str = f"\n\nContext: {json.dumps(req.context, ensure_ascii=False)}"

    raw = ""
    try:
        msg = _client.messages.create(
            model=MODEL_FAST,
            max_tokens=400,
            system=SYSTEM,
            messages=[{"role": "user", "content": f"Command: {req.transcript}{context_str}"}],
        )
        raw = msg.content[0].text.strip() if msg.content else ""
        import re
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Voice command JSON parse failed: %s | raw: %s", e, raw[:200])
        return {"action": "speak", "text": "Sorry, I didn't understand that. Try again."}
    except Exception as e:
        logger.error("Voice command error: %s", e, exc_info=True)
        raise HTTPException(500, "Voice command processing failed. Please try again.")
