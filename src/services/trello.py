"""Trello REST API client for the follow-ups machine.

Creates cards, lists boards/lists/members. Auth via API key + token
query parameters (TRELLO_API_KEY, TRELLO_API_TOKEN env vars).
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TRELLO_BASE = "https://api.trello.com/1"


def _backoff(attempt: int) -> float:
    return min((2 ** attempt) + random.uniform(0, 1), 30)


class TrelloClient:

    def __init__(self, api_key: Optional[str] = None, api_token: Optional[str] = None):
        self.api_key = api_key or os.getenv("TRELLO_API_KEY")
        self.api_token = api_token or os.getenv("TRELLO_API_TOKEN")
        if not self.api_key or not self.api_token:
            raise ValueError("TRELLO_API_KEY and TRELLO_API_TOKEN must be set.")
        self.http = httpx.Client(timeout=30)

    def close(self):
        self.http.close()

    def _auth_params(self) -> dict:
        return {"key": self.api_key, "token": self.api_token}

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> any:
        merged = {**self._auth_params(), **(params or {})}
        for attempt in range(retries):
            try:
                resp = self.http.get(url, params=merged)
                if resp.status_code == 429:
                    time.sleep(_backoff(attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if attempt < retries - 1:
                    time.sleep(_backoff(attempt))
                    continue
                raise RuntimeError(f"Trello request failed: {exc}") from exc
        return {}

    def _post(self, url: str, data: Optional[dict] = None, retries: int = 3) -> dict:
        merged = {**self._auth_params(), **(data or {})}
        for attempt in range(retries):
            try:
                resp = self.http.post(url, data=merged)
                if resp.status_code == 429:
                    time.sleep(_backoff(attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                if attempt < retries - 1:
                    time.sleep(_backoff(attempt))
                    continue
                raise RuntimeError(f"Trello request failed: {exc}") from exc
        return {}

    # ── Boards & Lists ───────────────────────────────────────────────────

    def list_boards(self) -> list[dict]:
        data = self._get(f"{TRELLO_BASE}/members/me/boards", {"fields": "id,name,url"})
        return [{"id": b["id"], "name": b["name"], "url": b.get("url", "")} for b in data]

    def list_board_lists(self, board_id: str) -> list[dict]:
        data = self._get(f"{TRELLO_BASE}/boards/{board_id}/lists", {"fields": "id,name"})
        return [{"id": l["id"], "name": l["name"]} for l in data]

    def list_board_members(self, board_id: str) -> list[dict]:
        data = self._get(f"{TRELLO_BASE}/boards/{board_id}/members", {"fields": "id,fullName,username"})
        return [{"id": m["id"], "name": m.get("fullName", ""), "username": m.get("username", "")} for m in data]

    # ── Cards ────────────────────────────────────────────────────────────

    def create_card(
        self,
        list_id: str,
        name: str,
        desc: str = "",
        due: Optional[str] = None,
        member_ids: Optional[list[str]] = None,
    ) -> dict:
        """Create a card on the specified list. Returns the created card."""
        card_data = {
            "idList": list_id,
            "name": name,
            "desc": desc,
            "pos": "top",
        }
        if due:
            card_data["due"] = due
        if member_ids:
            card_data["idMembers"] = ",".join(member_ids)

        result = self._post(f"{TRELLO_BASE}/cards", data=card_data)
        logger.info("Trello: created card '%s' → %s", name, result.get("url", ""))
        return {
            "id": result.get("id", ""),
            "url": result.get("url", ""),
            "name": result.get("name", name),
        }

    def find_card_by_name(self, list_id: str, name: str) -> Optional[dict]:
        """Search for a card by name in a specific list (for dedup)."""
        try:
            cards = self._get(f"{TRELLO_BASE}/lists/{list_id}/cards", {"fields": "id,name,url"})
            name_lower = name.lower().strip()
            for card in cards:
                if card.get("name", "").lower().strip() == name_lower:
                    return {"id": card["id"], "url": card.get("url", "")}
        except Exception as exc:
            logger.warning("Trello: failed to search cards in list %s: %s", list_id, exc)
        return None
