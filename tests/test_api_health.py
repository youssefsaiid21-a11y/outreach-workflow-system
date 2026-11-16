"""
Tests for the health check API endpoint.

Uses FastAPI's TestClient for real HTTP testing.
"""

import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client. Imports main lazily to avoid side effects."""
    from main import app
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Return auth headers using the API_KEY from .env (or a fallback for tests)."""
    api_key = os.getenv("API_KEY", "")
    return {"X-Api-Key": api_key} if api_key else {}


class TestLivenessEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/health/")
        assert resp.status_code == 200

    def test_has_status_ok(self, client):
        resp = client.get("/api/health/")
        data = resp.json()
        assert data["status"] == "ok"

    def test_has_timestamp(self, client):
        resp = client.get("/api/health/")
        data = resp.json()
        assert "timestamp" in data

    def test_no_auth_required(self, client):
        """Health endpoint should work without authentication."""
        resp = client.get("/api/health/")
        assert resp.status_code == 200


class TestFullHealthEndpoint:
    def test_returns_200(self, client, auth_headers):
        resp = client.get("/api/health/full", headers=auth_headers)
        assert resp.status_code == 200

    def test_has_checks(self, client, auth_headers):
        resp = client.get("/api/health/full", headers=auth_headers)
        data = resp.json()
        assert "checks" in data
        assert "database" in data["checks"]
        assert "api_keys" in data["checks"]

    def test_database_check_ok(self, client, auth_headers):
        resp = client.get("/api/health/full", headers=auth_headers)
        data = resp.json()
        assert data["checks"]["database"]["status"] == "ok"

    def test_overall_status_present(self, client, auth_headers):
        resp = client.get("/api/health/full", headers=auth_headers)
        data = resp.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")
