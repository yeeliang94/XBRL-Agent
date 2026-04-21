"""Shared PNG cache for rendered PDF pages.

Several notes sub-agents (and sometimes face-sheet agents) view the same
PDF page. Without a cache each agent re-renders the page to PNG bytes
and feeds it to the vision model. A FINCO Sheet-12 run observed pages
32, 33, and 37 rendered 3–4× each in under two minutes.

This module is a tiny thread-safe LRU that short-circuits repeat renders
without changing the public signature of `render_pages_to_png_bytes`
(which is still the only piece of code talking to PyMuPDF directly).

Design choices:
- **Keyed on (pdf_path, page, dpi)**: two agents on the same PDF at the
  same DPI will share renders; a different DPI is a different key so we
  never hand back bytes scaled for the wrong consumer.
- **Module-level singleton**: the cache survives across runs inside the
  same process, which is fine because distinct runs use distinct paths
  and the LRU bound caps memory.
- **Bounded by count, not bytes**: a page PNG at the rendering DPI runs
  ~100-300 KB. A cap of 64 entries ≈ 6-20 MB headroom — negligible.
- **Thread-safe**: renders are dispatched via `asyncio.to_thread`, so
  inserts can race. A single `threading.Lock` is plenty — contention is
  low because the critical section is O(1).

The cache is deliberately DUMB: no expiry, no hashing of file contents.
If the same path is overwritten mid-run we would serve stale bytes — but
that pattern does not exist in this codebase (uploads land in a
session-scoped output dir and are never rewritten).
"""
from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

# Cap is small by default; a full Sheet-12 run touches ~15-20 unique
# pages, so 64 gives comfortable headroom even with multi-run overlap.
# Override via env for operators who want to profile larger runs.
#
# Peer-review #6: clamp to a sane range so an operator typo
# (XBRL_PAGE_CACHE_MAX=9999999) can't balloon the cache into OOM
# territory. 4096 entries at typical PNG sizes (~200 KB each) caps
# memory at ~800 MB — the real limit is well below this in practice.
_DEFAULT_MAX_ENTRIES = 64


def _parse_max_entries_env(default: int) -> int:
    """Parse XBRL_PAGE_CACHE_MAX with a log-and-fallback on non-numeric input.

    Peer-review HIGH: a stray shell export like `XBRL_PAGE_CACHE_MAX=foo`
    used to crash module import at ``int(...)``, and because
    ``tools.page_cache`` is imported by every notes run, that crash
    prevented server startup entirely. Catch ValueError here so a typo
    degrades to the default instead of a hard fail.
    """
    raw = os.environ.get("XBRL_PAGE_CACHE_MAX")
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "XBRL_PAGE_CACHE_MAX=%r is not an integer — falling back to %d",
            raw, default,
        )
        return default


_MAX_ENTRIES_RAW = _parse_max_entries_env(_DEFAULT_MAX_ENTRIES)
_MAX_ENTRIES = max(1, min(_MAX_ENTRIES_RAW, 4096))
if _MAX_ENTRIES != _MAX_ENTRIES_RAW:
    logger.warning(
        "XBRL_PAGE_CACHE_MAX clamped from %d to %d (valid range 1-4096)",
        _MAX_ENTRIES_RAW, _MAX_ENTRIES,
    )


def _file_mtime_ns(pdf_path: str) -> int:
    """Stat-based content-change sentinel for cache keys.

    Peer-review #4: the cache survives across CLI runs inside a single
    process. If an operator runs
    ``python3 run.py data/FINCO.pdf`` twice against a path whose
    contents have been replaced (a common dev / back-to-back-filing
    workflow), without this sentinel the second run serves stale PNGs
    for the 2021 filing when processing the 2022 one.

    os.stat is cheap (sub-millisecond) and catches every real content
    change short of an atomic swap that preserves mtime to the
    nanosecond — which requires deliberate effort and is not a workflow
    we support.

    Returns 0 when the file is unreadable (e.g. the caller passed a
    synthetic test path). 0 still works as a key component; a second
    call with the same unreadable path gets a consistent cache hit
    rather than an error.
    """
    try:
        return int(os.stat(pdf_path).st_mtime_ns)
    except OSError:
        return 0


class _PageCache:
    """Singleton LRU. Private — callers use the module-level helpers.

    Key shape: ``(abspath, mtime_ns, page, dpi)``. The mtime component
    means two runs against the same path but different file contents
    correctly miss the cache instead of serving stale bytes.
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        # Key now includes mtime_ns: (abspath, mtime_ns, page, dpi).
        self._store: "OrderedDict[tuple[str, int, int, int], bytes]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries
        # Counters are purely informational; reset() zeroes them for tests.
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _build_key(pdf_path: str, page: int, dpi: int) -> tuple[str, int, int, int]:
        # Peer-review I-3: ``realpath`` (not ``abspath``) so the key
        # collapses symlink-equivalent paths to a single entry. On macOS
        # ``/tmp`` is a symlink to ``/private/tmp``; a PDF written to
        # one and read through the other would otherwise double-render
        # every page. ``realpath`` resolves the underlying inode path
        # once so both callers share the same cache slot.
        real = os.path.realpath(pdf_path)
        return (real, _file_mtime_ns(real), page, dpi)

    def get(self, pdf_path: str, page: int, dpi: int) -> Optional[bytes]:
        key = self._build_key(pdf_path, page, dpi)
        with self._lock:
            if key not in self._store:
                self.misses += 1
                return None
            # Touch: move to most-recently-used end.
            self._store.move_to_end(key)
            self.hits += 1
            return self._store[key]

    def set(self, pdf_path: str, page: int, dpi: int, data: bytes) -> None:
        key = self._build_key(pdf_path, page, dpi)
        with self._lock:
            if key in self._store:
                # Refresh MRU position without duplicating data.
                self._store.move_to_end(key)
                return
            self._store[key] = data
            if len(self._store) > self._max:
                # popitem(last=False) drops the least-recently-used entry.
                self._store.popitem(last=False)

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def reset(self) -> None:
        """Clear the cache and zero the counters. Used by tests."""
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_INSTANCE = _PageCache()


def get(pdf_path: str, page: int, dpi: int) -> Optional[bytes]:
    return _INSTANCE.get(pdf_path, page, dpi)


def put(pdf_path: str, page: int, dpi: int, data: bytes) -> None:
    """Store rendered PNG bytes."""
    _INSTANCE.set(pdf_path, page, dpi, data)


# Backwards-compat alias for the prior `set` name. Kept so external
# callers that haven't migrated yet don't break (peer-review S7
# rename — the primary name is now `put`).
set = put  # noqa: A001


def stats() -> dict:
    """Return a small dict for logs / cost reports."""
    return {
        "hits": _INSTANCE.hits,
        "misses": _INSTANCE.misses,
        "hit_rate": _INSTANCE.hit_rate(),
        "size": len(_INSTANCE),
    }


def reset() -> None:
    """Test-only: empty the cache and zero stats."""
    _INSTANCE.reset()
