"""
Conference sub-package router shim.

P4-1 / audit #6 MANUAL-1+5: the old ``src/api/conference.py`` was a
1805-LOC god-module mixing routing, parsing, AI prompts, draft dispatch,
and JSON validation. This package keeps the same external interface
(``from src.api.conference import router``) and the same URL prefix
(``/conference/...``) while the internals live in focused sub-modules:

- ``parser.py``   -  spreadsheet parsing (pure functions, no DB)
- ``prompts.py``  -  prompt fragments + banned phrases (constants)
- ``service.py``  -  style block, AI generation, draft dispatch
- ``_shared.py``  -  request models, locks, helpers
- ``routes_inboxes.py``   -  inbox CRUD + test
- ``routes_leads.py``     -  import, list, enrich, costs, save email
- ``routes_generate.py``  -  generate email + sequence
- ``routes_drafts.py``    -  push drafts to Outlook / IMAP

URL stability is preserved: every existing endpoint still resolves to
the same path. The router below mounts the sub-routers under the
``/conference`` prefix, so callers and the existing tests continue to
work without any changes.
"""
from __future__ import annotations

from fastapi import APIRouter

from .routes_drafts import router as _drafts_router
from .routes_generate import router as _generate_router
from .routes_inboxes import router as _inboxes_router
from .routes_leads import router as _leads_router
from .routes_replies import router as _replies_router  # Events-reply-cutoff 2026-04-09
# Back-compat re-exports for tests / external callers that imported
# private helpers from the old monolithic module.
from .service import (  # noqa: F401  (re-exported)
    _generate_email,
    _generate_sequence,
    _get_style_block,
    _load_seq_steps,
    _parse_sequence_json,
    _validate_sequence,
)
from .parser import (  # noqa: F401  (re-exported)
    _parse_csv,
    _parse_excel,
    _smart_parse,
)

router = APIRouter(prefix="/conference", tags=["conference"])
router.include_router(_inboxes_router)
router.include_router(_leads_router)
router.include_router(_generate_router)
router.include_router(_drafts_router)
router.include_router(_replies_router)


__all__ = ["router"]
