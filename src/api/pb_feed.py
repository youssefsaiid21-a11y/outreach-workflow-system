"""
CSV feed endpoints consumed by PhantomBuster phantoms.

This module serves two CSV endpoints that PhantomBuster reads to get its
list of leads to process:

  GET /api/pb/connect-leads.csv  → LinkedIn Auto Connect phantom reads this
  GET /api/pb/message-leads.csv  → LinkedIn Message Sender phantom reads this

HOW THIS FITS IN THE SYSTEM:
  1. Orchestration handlers queue LinkedIn actions to the linkedin_queue table
  2. The linkedin_worker marks queue items as "processing"
  3. These endpoints serve ALL processing items as CSV rows
  4. PB's phantom fetches the CSV URL on each launch and processes the URLs
  5. The linkedin_worker polls PB results to confirm delivery and mark items "done"

The connect CSV has one column: linkedinProfile (the LinkedIn profile URL).
The message CSV has two columns: profileUrl and message (per-lead personalised text).

SECURITY:
  Requires a ``secret`` query parameter matching the WEBHOOK_SECRET env var.
  PB phantom URLs must include ``?secret=<WEBHOOK_SECRET>``. If WEBHOOK_SECRET
  is not configured, the feed returns 403 (disabled by default).

MULTI-USER:
  Pass ``?user_id=<id>`` to scope the feed to one user's leads. Each user's
  PB phantom should be configured with their own URL including their user_id.

RESPONSE FORMAT:
  Returns a fully-buffered response with explicit Content-Length (not chunked).
  PhantomBuster rejects chunked/streaming responses — it needs to see the full
  file size upfront to validate the URL as "publicly accessible".
"""
import csv
import hmac
import io
import logging
import os

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response


def _csv_response(body: str, filename: str) -> Response:
    """Return a CSV as a plain, fully-buffered response with an explicit
    Content-Length.

    PhantomBuster (and other strict server-side fetchers/validators) reject
    chunked / streaming responses that lack a Content-Length, even though
    browsers and curl accept them. Serving a sized body makes the feed
    behave like a normal downloadable CSV file so PB's "A URL" lead source
    validates as publicly accessible.
    """
    data = body.encode("utf-8")
    return Response(
        content=data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
            # PhantomBuster validates the "A URL" lead source by fetching it
            # from the browser; a permissive CORS header lets that fetch
            # succeed (otherwise it's blocked and the URL shows as
            # inaccessible). Safe: the feed is public, read-only, no cookies.
            "Access-Control-Allow-Origin": "*",
        },
    )
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import LinkedInQueue, LinkedInQueueStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pb", tags=["phantombuster-feed"])


def _check_pb_secret(secret: str) -> None:
    """Validate secret parameter against WEBHOOK_SECRET env var.

    SWAT-C1: WEBHOOK_SECRET is now mandatory. Feed is disabled if unset,
    and requests without a valid ?secret= param are rejected.
    """
    from fastapi import HTTPException

    expected = os.getenv("WEBHOOK_SECRET", "").strip()
    if not expected:
        raise HTTPException(403, "WEBHOOK_SECRET is not configured. CSV feed is disabled.")
    if not secret:
        raise HTTPException(403, "Missing ?secret= parameter.")
    if not hmac.compare_digest(secret, expected):
        raise HTTPException(403, "Invalid secret.")


@router.api_route("/connect-leads.csv", methods=["GET", "HEAD"])
def connect_leads_csv(
    user_id: Optional[str] = Query(None, description="Filter by user_id for multi-user deployments"),
    secret: str = Query("", description="Optional WEBHOOK_SECRET for access control"),
    db: Session = Depends(get_db),
):
    """Return all pending LinkedIn connect leads as CSV for PB to ingest.

    Includes both ``queued`` and ``processing`` items so the phantom
    can re-ingest any leads that were lost from its internal storage.

    Pass ``?user_id=<id>`` to scope the feed to a single user's leads.
    Each user's PB phantom should use their own URL.
    """
    _check_pb_secret(secret)
    q = db.query(LinkedInQueue).filter(
        LinkedInQueue.action_type == "connect",
        LinkedInQueue.status.in_([
            LinkedInQueueStatus.queued,
            LinkedInQueueStatus.processing,
        ]),
        LinkedInQueue.linkedin_url.isnot(None),
    )
    if user_id:
        q = q.filter(LinkedInQueue.user_id == user_id)
    # SWAT-L3: deterministic FIFO ordering
    items = q.order_by(LinkedInQueue.created_at.asc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["linkedinProfile"])  # PB reads this column
    for item in items:
        url = (item.linkedin_url or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if url:
            writer.writerow([url])

    return _csv_response(buf.getvalue(), "connect-leads.csv")


@router.api_route("/message-leads.csv", methods=["GET", "HEAD"])
def message_leads_csv(
    user_id: Optional[str] = Query(None, description="Filter by user_id for multi-user deployments"),
    secret: str = Query("", description="Optional WEBHOOK_SECRET for access control"),
    db: Session = Depends(get_db),
):
    """Return all pending LinkedIn message leads as CSV for PB to ingest.

    The Message Sender phantom requires two columns:
      - profileUrl: the LinkedIn profile URL
      - message: the personalised message text per lead

    Includes both ``queued`` and ``processing`` items so the phantom
    can re-ingest any leads that were lost from its internal storage.

    Pass ``?user_id=<id>`` to scope the feed to a single user's leads.
    """
    _check_pb_secret(secret)
    from ..db.models import EmailSequence

    q = db.query(LinkedInQueue).filter(
        LinkedInQueue.action_type == "message",
        LinkedInQueue.status.in_([
            LinkedInQueueStatus.queued,
            LinkedInQueueStatus.processing,
        ]),
        LinkedInQueue.linkedin_url.isnot(None),
    )
    if user_id:
        q = q.filter(LinkedInQueue.user_id == user_id)
    # SWAT-L3: deterministic FIFO ordering
    items = q.order_by(LinkedInQueue.created_at.asc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["profileUrl", "message"])
    for item in items:
        # Normalise URL scheme: PB requires HTTPS URLs
        url = (item.linkedin_url or "").strip()
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if not url:
            continue
        # The message text is stored on the queue row when the handler queues the action.
        # If it's missing (e.g., older queue rows or manual imports), fall back to the
        # EmailSequence.linkedin_message field which is populated during campaign generation.
        msg = (item.message_text or "").strip()
        if not msg:
            seq = (
                db.query(EmailSequence)
                .filter_by(prospect_id=item.prospect_id)
                .order_by(EmailSequence.created_at.desc())
                .first()
            )
            if seq:
                msg = (seq.linkedin_message or "").strip()
        if not msg:
            # No message text available — exclude from CSV. The poller will eventually
            # timeout this item after 24h+ and mark it failed, which triggers
            # _pause_orch_for_failed_linkedin (logging only, sequence continues).
            continue
        writer.writerow([url, msg])

    return _csv_response(buf.getvalue(), "message-leads.csv")
