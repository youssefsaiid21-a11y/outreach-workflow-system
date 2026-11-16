"""
P5 - dashboard asset smoke tests.

After Phase 5, the dashboard's CSS and JS live in their own files
(``dashboard/styles.css`` and ``dashboard/main.js``) instead of being
embedded inside ``dashboard/index.html``. The shell HTML went from
9554 LOC down to ~1900 LOC. These tests verify the new layout serves
correctly via FastAPI's StaticFiles mount and the SPA middleware.

What this catches:

1. The shell ``index.html`` still references both extracted assets via
   the cache-bust placeholders.
2. The Python middleware substitutes the placeholders with real mtime
   integers - no literal ``__STYLES_VERSION__`` makes it to the
   browser.
3. The CSS and JS files are reachable via the ``/assets/`` mount.
4. The CSS file looks like CSS (has ``--navy``) and the JS file looks
   like JS (has ``checkAuth`` - the bootstrap entry point).
5. The shell does NOT contain inline ``<style>`` or ``<script>`` blocks
   from the old monolith - protects against an accidental revert.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """TestClient without the ``with`` context manager so we don't trigger
    the app lifespan - the lifespan starts the LinkedIn worker thread,
    whose module-level stop_event would then leak into other tests in
    the same process and break ``test_linkedin_worker_user_id``."""
    # Import lazily so the env var above is set first.
    import main as _main  # noqa: F401
    return TestClient(_main.app)


def _dashboard_dir() -> Path:
    return Path(_HERE).parent / "dashboard"


def test_styles_css_extracted_to_disk():
    css = _dashboard_dir() / "styles.css"
    assert css.exists(), "P5: styles.css must be extracted from index.html"
    body = css.read_text(encoding="utf-8")
    assert "--navy" in body, "styles.css should retain the brand CSS variables"
    # Sanity: no leftover <style> tags inside the extracted file.
    assert "<style>" not in body
    assert "</style>" not in body


def test_main_js_extracted_to_disk():
    js = _dashboard_dir() / "main.js"
    assert js.exists(), "P5: main.js must be extracted from index.html"
    body = js.read_text(encoding="utf-8")
    # checkAuth() is the bootstrap entry point at the bottom of the file.
    assert "checkAuth" in body
    # No leftover <script> tags inside the extracted file.
    assert "<script>" not in body
    assert "</script>" not in body


def test_index_html_is_a_shell_not_a_monolith():
    html = (_dashboard_dir() / "index.html").read_text(encoding="utf-8")
    line_count = html.count("\n")
    # Pre-P5 the file was ~9554 lines. The shell should be substantially
    # smaller. Allow generous head-room (3000) so future markup additions
    # don't fail this test for the wrong reason.
    assert line_count < 3000, (
        f"index.html should be a small shell, got {line_count} lines - "
        "did the CSS/JS extraction get reverted?"
    )
    # The shell links the extracted assets via the cache-bust placeholders.
    assert "/assets/styles.css?v=" in html
    assert "/assets/main.js?v=" in html
    # And does NOT carry an inline <style>...</style> block any more.
    assert "<style>" not in html
    # The only remaining <script ...> tag should be the external bundle.
    # Inline <script>...</script> blocks would indicate a partial revert.
    assert "<script>\n" not in html


def test_root_serves_shell_with_substituted_versions(client):
    """The SPA middleware should substitute __STYLES_VERSION__ /
    __MAIN_VERSION__ with real integer mtimes. The literal placeholders
    must NOT reach the browser."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "__STYLES_VERSION__" not in body, "version placeholder leaked to client"
    assert "__MAIN_VERSION__" not in body, "version placeholder leaked to client"
    # Shell still references both assets with a real cache-bust value.
    assert "/assets/styles.css?v=" in body
    assert "/assets/main.js?v=" in body


def test_styles_asset_reachable(client):
    resp = client.get("/assets/styles.css")
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/css")
    assert len(resp.content) > 1000, "styles.css served but suspiciously small"
    assert b"--navy" in resp.content


def test_main_js_asset_reachable(client):
    resp = client.get("/assets/main.js")
    assert resp.status_code == 200
    ctype = resp.headers.get("content-type", "")
    assert "javascript" in ctype or "text/plain" in ctype, (
        f"main.js served with unexpected content-type: {ctype}"
    )
    assert len(resp.content) > 10000, "main.js served but suspiciously small"
    assert b"checkAuth" in resp.content
