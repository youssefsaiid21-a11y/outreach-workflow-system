"""
Provider abstractions and concrete implementations for external channels.

Each channel (LinkedIn, WhatsApp) is represented by an abstract base class.
Concrete implementations wire up real API clients.  Stub implementations let
the system run without a channel configured  -  all stub calls return a
not_connected result rather than raising an exception.

This file deliberately avoids importing heavy dependencies at module level so
that unit tests can import it without needing all environment variables set.

Note: Email sending (conference outreach) runs through the Graph API directly
and does not go through the orchestration provider layer.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .enums import ProviderStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class LinkedInResult:
    """Result returned by any LinkedInProvider method."""

    success: bool
    status: ProviderStatus = ProviderStatus.ok
    error: str | None = None


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------


class LinkedInProvider(ABC):
    """Abstract LinkedIn automation provider."""

    @abstractmethod
    def send_connect_request(
        self,
        lead_id: str,
        linkedin_url: str,
        note_template_id: str | None,
    ) -> LinkedInResult:
        """
        Send a LinkedIn connection request to the given profile URL.

        Args:
            lead_id:          Internal lead identifier.
            linkedin_url:     Full LinkedIn profile URL.
            note_template_id: Optional personalised connection note template.

        Returns:
            LinkedInResult indicating success or the specific failure status.
        """

    @abstractmethod
    def send_linkedin_message(
        self,
        lead_id: str,
        linkedin_url: str,
        template_id: str,
    ) -> LinkedInResult:
        """
        Send a direct LinkedIn message to a 1st-degree connection.

        Args:
            lead_id:      Internal lead identifier.
            linkedin_url: Full LinkedIn profile URL.
            template_id:  Message template identifier.

        Returns:
            LinkedInResult indicating success or the specific failure status.
        """

    @abstractmethod
    def check_connection_status(
        self,
        lead_id: str,
        linkedin_url: str,
    ) -> bool:
        """
        Check whether the lead has accepted our connection request.

        Args:
            lead_id:      Internal lead identifier.
            linkedin_url: Full LinkedIn profile URL.

        Returns:
            True if connected, False otherwise.
        """


class WhatsAppProvider(ABC):
    """
    Abstract WhatsApp messaging provider.

    TODO: wire to Twilio / 360dialog when the channel is activated.
    """

    @abstractmethod
    def send_message(
        self,
        lead_id: str,
        phone: str,
        template_id: str,
    ) -> dict[str, Any]:
        """
        Send a WhatsApp template message to the lead's phone number.

        Args:
            lead_id:     Internal lead identifier.
            phone:       E.164 phone number (e.g. "+966501234567").
            template_id: Approved WhatsApp message template name.

        Returns:
            Raw response dict from the downstream provider.
        """


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class PhantomBusterLinkedInProvider(LinkedInProvider):
    """
    LinkedIn provider backed by PhantomBuster  -  launches Phantoms via API
    for connection requests and messages.

    Requires one-time setup in PhantomBuster UI:
      1. Create a LinkedIn Auto Connect Phantom → note its agent ID
      2. Create a LinkedIn Message Sender Phantom → note its agent ID
      3. Configure LinkedIn session cookie in each Phantom

    Set in .env:
      PHANTOMBUSTER_CONNECT_AGENT_ID=<auto-connect phantom id>
      PHANTOMBUSTER_MESSAGE_AGENT_ID=<message-sender phantom id>
    """

    def __init__(
        self,
        api_key: str | None = None,
        connect_agent_id: str | None = None,
        message_agent_id: str | None = None,
    ):
        """P3-2: accept per-user credentials so two reps' Phantoms don't
        share state via the env-var global fallback."""
        import os
        self._api_key = api_key or os.getenv("PHANTOMBUSTER_API_KEY", "")
        self._connect_agent_id = connect_agent_id or os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", "")
        self._message_agent_id = message_agent_id or os.getenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from ..services.phantombuster import PhantomBusterClient
            self._client = PhantomBusterClient(api_key=self._api_key or None)
        return self._client

    def send_connect_request(
        self,
        lead_id: str,
        linkedin_url: str,
        note_template_id: str | None,
    ) -> LinkedInResult:
        """Launch LinkedIn Auto Connect Phantom for this lead."""
        try:
            client = self._get_client()
            if not self._connect_agent_id:
                return LinkedInResult(
                    success=False,
                    status=ProviderStatus.not_connected,
                    error="PHANTOMBUSTER_CONNECT_AGENT_ID not set in .env",
                )

            # Bug #15 fix: Outbound Agent uses leadsSourceUrl, not profileUrls.
            url = linkedin_url.strip()
            if url.startswith("http://"):
                url = "https://" + url[7:]
            result = client.launch_agent(
                agent_id=self._connect_agent_id,
                bonus_argument={
                    "leadsSourceUrl": url,
                    "message": note_template_id or "",
                },
            )
            container_id = result.get("containerId", "")
            logger.info(
                "PhantomBuster: launched connect for lead %s (container=%s)",
                lead_id, container_id,
            )
            return LinkedInResult(success=True, status=ProviderStatus.ok)

        except Exception as exc:
            logger.error("PhantomBuster connect failed for %s: %s", lead_id, exc)
            return LinkedInResult(
                success=False,
                status=ProviderStatus.not_connected,
                error=str(exc),
            )

    def send_linkedin_message(
        self,
        lead_id: str,
        linkedin_url: str,
        template_id: str,
    ) -> LinkedInResult:
        """Launch LinkedIn Message Sender Phantom for this lead."""
        try:
            client = self._get_client()
            if not self._message_agent_id:
                return LinkedInResult(
                    success=False,
                    status=ProviderStatus.not_connected,
                    error="PHANTOMBUSTER_MESSAGE_AGENT_ID not set in .env",
                )

            result = client.launch_agent(
                agent_id=self._message_agent_id,
                bonus_argument={
                    "profileUrls": [linkedin_url],
                    "numberOfProfiles": 1,
                    "message": template_id,
                },
            )
            container_id = result.get("containerId", "")
            logger.info(
                "PhantomBuster: launched message for lead %s (container=%s)",
                lead_id, container_id,
            )
            return LinkedInResult(success=True, status=ProviderStatus.ok)

        except Exception as exc:
            logger.error("PhantomBuster message failed for %s: %s", lead_id, exc)
            return LinkedInResult(
                success=False,
                status=ProviderStatus.not_connected,
                error=str(exc),
            )

    def check_connection_status(
        self,
        lead_id: str,
        linkedin_url: str,
    ) -> bool:
        """Check connection status via local DB (updated by PhantomBuster result polling)."""
        try:
            from ..db.database import SessionLocal
            from ..db.models import Prospect
            db = SessionLocal()
            try:
                prospect = db.query(Prospect).filter_by(id=lead_id).first()
                return bool(getattr(prospect, "linkedin_connected", False)) if prospect else False
            finally:
                db.close()
        except Exception:
            return False


class StubLinkedInProvider(LinkedInProvider):
    """
    No-op LinkedIn provider returned when no real provider is configured.

    All methods return not_connected so the orchestration layer can still
    run sequences  -  LinkedIn steps will be skipped or converted to manual
    tasks rather than raising unhandled exceptions.
    """

    _MSG = "LinkedIn provider not configured"

    def send_connect_request(
        self,
        lead_id: str,
        linkedin_url: str,
        note_template_id: str | None,
    ) -> LinkedInResult:
        """Return not_connected  -  LinkedIn automation is not wired up."""
        logger.warning("StubLinkedInProvider.send_connect_request called for lead %s", lead_id)
        return LinkedInResult(
            success=False,
            status=ProviderStatus.not_connected,
            error=self._MSG,
        )

    def send_linkedin_message(
        self,
        lead_id: str,
        linkedin_url: str,
        template_id: str,
    ) -> LinkedInResult:
        """Return not_connected  -  LinkedIn automation is not wired up."""
        logger.warning("StubLinkedInProvider.send_linkedin_message called for lead %s", lead_id)
        return LinkedInResult(
            success=False,
            status=ProviderStatus.not_connected,
            error=self._MSG,
        )

    def check_connection_status(
        self,
        lead_id: str,
        linkedin_url: str,
    ) -> bool:
        """Always returns False  -  cannot check status without a real provider."""
        return False
