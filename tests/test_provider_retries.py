"""
P2-1 / deep audit CRITICAL-4 - transient httpx errors must be retried.

Verifies that PhantomBusterClient and OutlookClient retry on
httpx.TransportError (TimeoutException, ConnectError, ReadError, etc.)
instead of propagating immediately.

Note: InstantlyClient has been removed from the project. Those retry tests
have been deleted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("PHANTOMBUSTER_API_KEY", "test_phantom_key")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralise time.sleep so tests don't block on backoff."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)


def _fake_response(status: int = 200, payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.content = b"{}"
    resp.json.return_value = payload or {"ok": True}
    resp.raise_for_status = MagicMock()
    return resp


def test_phantombuster_get_retries_on_timeout():
    from src.services.phantombuster import PhantomBusterClient

    client = PhantomBusterClient(api_key="test")
    call_count = {"n": 0}

    def flaky_get(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.TimeoutException("slow")
        return _fake_response(200, {"data": []})

    with patch.object(client.client, "get", side_effect=flaky_get):
        result = client._get("/agents/fetch-all")

    assert call_count["n"] == 3
    assert result == {"data": []}


def test_phantombuster_post_retries_on_remote_protocol_error():
    from src.services.phantombuster import PhantomBusterClient

    client = PhantomBusterClient(api_key="test")
    call_count = {"n": 0}

    def flaky_post(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.RemoteProtocolError("peer closed")
        return _fake_response(200, {"containerId": "c1"})

    with patch.object(client.client, "post", side_effect=flaky_post):
        result = client._post("/agents/launch", {"id": "a1"})

    assert call_count["n"] == 2
    assert result == {"containerId": "c1"}


def test_outlook_get_retries_on_timeout():
    from src.services import outlook as outlook_mod

    # Avoid OutlookClient.__init__ (requires MICROSOFT_CLIENT_ID and msal).
    client = outlook_mod.OutlookClient.__new__(outlook_mod.OutlookClient)
    client.http = MagicMock()
    client._access_token = "fake_token"
    client._get_token = lambda: "fake_token"  # type: ignore[assignment]

    call_count = {"n": 0}

    def flaky_get(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.TimeoutException("slow")
        return _fake_response(200, {"value": []})

    client.http.get = MagicMock(side_effect=flaky_get)

    result = client._get("https://graph.microsoft.com/v1.0/me/messages")
    assert call_count["n"] == 3
    assert result == {"value": []}
