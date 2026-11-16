"""
PhantomBuster management endpoints  -  /api/phantombuster/*

Provides operator access to PB phantom status, agent listings, and
manual launch/stop controls. All destructive actions (launch/stop)
require admin role.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_api_key, get_current_user
from ..db.models import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/phantombuster",
    tags=["phantombuster"],
    dependencies=[Depends(require_api_key)],
)


def _require_admin(current_user: User | None) -> None:
    if current_user is None or current_user.role not in (UserRole.admin, UserRole.super_admin):
        raise HTTPException(status_code=403, detail="Admin access required.")


def _get_pb_client():
    from ..services.phantombuster import PhantomBusterClient
    key = os.getenv("PHANTOMBUSTER_API_KEY", "").strip()
    if not key:
        raise HTTPException(400, "PHANTOMBUSTER_API_KEY not configured. Add it to your .env file.")
    return PhantomBusterClient(api_key=key)


@router.get("/status")
def phantombuster_status():
    """Check if PhantomBuster is configured and the API key is valid."""
    api_key = os.getenv("PHANTOMBUSTER_API_KEY", "").strip()
    if not api_key:
        return {"configured": False, "connected": False, "error": "PHANTOMBUSTER_API_KEY not set in .env"}
    try:
        client = _get_pb_client()
        ok = client.check_api_key()
        client.close()
        return {
            "configured": True,
            "connected": ok,
            "connect_agent_id": os.getenv("PHANTOMBUSTER_CONNECT_AGENT_ID", "") or None,
            "message_agent_id": os.getenv("PHANTOMBUSTER_MESSAGE_AGENT_ID", "") or None,
        }
    except Exception as e:
        logger.error("PhantomBuster status check failed: %s", e, exc_info=True)
        return {"configured": True, "connected": False, "error": "Connection check failed. See server logs."}


@router.get("/agents")
def phantombuster_agents():
    """List all Phantoms in the workspace."""
    client = _get_pb_client()
    try:
        return client.list_agents()
    finally:
        client.close()


@router.get("/agents/{agent_id}/output")
def phantombuster_agent_output(agent_id: str):
    """Get the latest run output for a Phantom."""
    client = _get_pb_client()
    try:
        return client.get_run_results(agent_id)
    finally:
        client.close()


@router.post("/agents/{agent_id}/launch")
def phantombuster_launch(
    agent_id: str,
    request_body: dict | None = None,
    current_user: User = Depends(get_current_user),
):
    """Launch a Phantom with optional bonusArgument overrides. Admin only."""
    _require_admin(current_user)
    client = _get_pb_client()
    try:
        bonus = (request_body or {}).get("bonusArgument")
        return client.launch_agent(agent_id, bonus_argument=bonus)
    finally:
        client.close()


@router.post("/agents/{agent_id}/stop")
def phantombuster_stop(
    agent_id: str,
    current_user: User = Depends(get_current_user),
):
    """Stop a running Phantom. Admin only."""
    _require_admin(current_user)
    client = _get_pb_client()
    try:
        return client.stop_agent(agent_id)
    finally:
        client.close()


@router.get("/containers/{container_id}/result")
def phantombuster_container_result(container_id: str):
    """Get the structured result object from a completed container."""
    client = _get_pb_client()
    try:
        return client.get_container_result(container_id)
    finally:
        client.close()
