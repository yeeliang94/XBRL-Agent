"""Content fingerprints for notes-cell writes (Harness-learnings Item 5).

A 12-hex-char sha256 prefix over the cell HTML — the same shape the
pydantic-ai-harness filesystem tools use for optimistic-concurrency
(read returns a fingerprint; a stale write is refused with "re-read and
retry"). Our first adoption is ADVISORY ONLY (open question F.4 in
docs/PLAN-pydantic-ai-v2.md): `persist_notes_cells` uses fingerprints to
DETECT two writers landing different content on the same (sheet, row)
in one batch — the Sheet-12 fan-out's silent last-write-wins case — and
logs it loudly without changing the outcome. Enforcement (refuse +
ModelRetry) is a later, evidence-gated step.

Kill switch: ``XBRL_WRITE_FRESHNESS`` — ``advisory`` (default) | ``off``.
"""

from __future__ import annotations

import hashlib
import os


def content_fingerprint(text: str) -> str:
    """Stable 12-hex-char fingerprint of a cell's content."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def freshness_mode() -> str:
    """``off`` silences collision detection; anything else is advisory."""
    raw = os.environ.get("XBRL_WRITE_FRESHNESS", "advisory").strip().lower()
    return "off" if raw == "off" else "advisory"
