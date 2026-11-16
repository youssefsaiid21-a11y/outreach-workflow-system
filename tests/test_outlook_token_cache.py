"""
P3-3: OutlookClient must compute a per-user token cache path and must
refuse to instantiate without either a user_id or an explicit override.
"""
from pathlib import Path

import pytest

from src.services import outlook as outlook_mod
from src.services.outlook import OutlookClient, TOKEN_DIR


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    # MICROSOFT_CLIENT_ID is required by the constructor; stub it so the
    # test doesn't depend on .env.
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "stub-client-id")
    # Prevent the constructor from reading/writing a real cache file on
    # disk even if one exists. We only care about the computed path here.
    monkeypatch.setattr(Path, "exists", lambda self: False)


def test_per_user_token_cache_path(monkeypatch, tmp_path):
    """OutlookClient(user_id=...) computes outreach-system/tokens/outlook_<id>.json."""
    # mkdir should be a no-op (we just verify the path the client derived)
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    client = OutlookClient(user_id="u1")
    expected = TOKEN_DIR / "outlook_u1.json"
    assert client.token_file == expected
    assert client.token_file.name == "outlook_u1.json"


def test_two_users_get_separate_caches(monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    a = OutlookClient(user_id="user_a")
    b = OutlookClient(user_id="user_b")
    assert a.token_file != b.token_file
    assert a.token_file.name == "outlook_user_a.json"
    assert b.token_file.name == "outlook_user_b.json"


def test_explicit_token_file_override_still_works(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    custom = tmp_path / "custom_token.json"
    client = OutlookClient(token_file=custom)
    assert client.token_file == custom


def test_missing_user_id_and_no_override_raises(monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    with pytest.raises(ValueError, match="user_id"):
        OutlookClient()
