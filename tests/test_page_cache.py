"""Phase 3 (post-FINCO-2021 audit) — shared PNG page cache.

`tools/page_cache.py` is a process-wide LRU of rendered PDF pages keyed
on `(pdf_path, page, dpi)`. Sub-agents on the same PDF and DPI should
reuse each other's renders. These tests pin:

- Miss-then-hit behaviour + stats bookkeeping.
- LRU eviction order (oldest-unused drops first).
- DPI participates in the key (cache isolation across DPIs).
- Path participates in the key (cache isolation across files).

The integration (does `notes._render_pages_async` actually consult the
cache?) is exercised by `test_render_pages_async_uses_cache` below,
which monkey-patches the underlying renderer to count calls.
"""
from __future__ import annotations

import asyncio

import pytest

from tools import page_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test gets a clean cache + zeroed stats."""
    page_cache.reset()
    yield
    page_cache.reset()


def test_miss_returns_none_and_increments_miss_counter():
    assert page_cache.get("a.pdf", 1, 200) is None
    assert page_cache.stats()["misses"] == 1
    assert page_cache.stats()["hits"] == 0


def test_set_then_get_returns_bytes_and_increments_hit_counter():
    page_cache.set("a.pdf", 1, 200, b"\x89PNG-fake")
    assert page_cache.get("a.pdf", 1, 200) == b"\x89PNG-fake"
    assert page_cache.stats()["hits"] == 1


def test_different_dpi_is_a_different_entry():
    """A 100-DPI render and a 200-DPI render must not alias each other —
    the downstream consumer expects bytes at the DPI it asked for."""
    page_cache.set("a.pdf", 1, 100, b"small")
    page_cache.set("a.pdf", 1, 200, b"big")
    assert page_cache.get("a.pdf", 1, 100) == b"small"
    assert page_cache.get("a.pdf", 1, 200) == b"big"


def test_different_path_is_a_different_entry():
    page_cache.set("a.pdf", 1, 200, b"A")
    page_cache.set("b.pdf", 1, 200, b"B")
    assert page_cache.get("a.pdf", 1, 200) == b"A"
    assert page_cache.get("b.pdf", 1, 200) == b"B"


def test_lru_eviction_drops_least_recently_used():
    """Flood the cache past its max and confirm the LRU entry is evicted.
    We temporarily shrink the cap so the test runs fast."""
    from tools.page_cache import _INSTANCE
    original_max = _INSTANCE._max
    _INSTANCE._max = 3
    try:
        page_cache.set("a.pdf", 1, 200, b"A")
        page_cache.set("a.pdf", 2, 200, b"B")
        page_cache.set("a.pdf", 3, 200, b"C")
        # Touch A so it becomes MRU; B is now the LRU.
        assert page_cache.get("a.pdf", 1, 200) == b"A"
        page_cache.set("a.pdf", 4, 200, b"D")  # evicts B
        assert page_cache.get("a.pdf", 2, 200) is None
        assert page_cache.get("a.pdf", 1, 200) == b"A"
        assert page_cache.get("a.pdf", 3, 200) == b"C"
        assert page_cache.get("a.pdf", 4, 200) == b"D"
    finally:
        _INSTANCE._max = original_max


def test_mtime_change_invalidates_cache(tmp_path):
    """Peer-review #4: when a PDF on disk is replaced (same path, new
    contents), the cache must NOT serve stale PNG bytes. Since the key
    includes mtime_ns, a file-mtime bump forces a miss on the next get."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%% v1\n")

    # Populate the cache for v1.
    page_cache.set(str(pdf), 1, 200, b"PNG:v1")
    assert page_cache.get(str(pdf), 1, 200) == b"PNG:v1"

    # Overwrite the file with different contents and a different mtime.
    # os.utime gives us full control over the mtime — on some
    # filesystems writing the bytes alone leaves mtime at the same
    # nanosecond on a fast machine, which would mask the test.
    import os
    pdf.write_bytes(b"%PDF-1.4\n%% v2 - completely different report\n")
    stat = os.stat(str(pdf))
    os.utime(str(pdf), ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    # Now the cache must miss — we'd otherwise serve v1 bytes for a v2 file.
    assert page_cache.get(str(pdf), 1, 200) is None, (
        "cache served stale bytes after mtime bump — key must include mtime"
    )


def test_symlinked_path_shares_cache_entry(tmp_path):
    """Peer-review I-3: on macOS ``/tmp`` is a symlink to ``/private/tmp``.
    A PDF written under one path and read through the other must share
    one cache slot, not double-render every page. ``realpath`` in
    ``_build_key`` resolves both forms to the same inode path.

    The test builds a concrete symlink in tmp_path so the behaviour
    is platform-independent.
    """
    import os

    real_pdf = tmp_path / "real" / "report.pdf"
    real_pdf.parent.mkdir()
    real_pdf.write_bytes(b"%PDF-1.4\n")

    symlinked_pdf = tmp_path / "via_symlink.pdf"
    os.symlink(str(real_pdf), str(symlinked_pdf))

    # Populate via the real path.
    page_cache.set(str(real_pdf), 1, 200, b"PNG:shared")
    # Read via the symlink — must hit the same cache slot.
    hit = page_cache.get(str(symlinked_pdf), 1, 200)
    assert hit == b"PNG:shared", (
        "symlink and target must resolve to the same cache key"
    )
    assert page_cache.stats()["hits"] == 1
    assert page_cache.stats()["misses"] == 0


def test_reset_clears_store_and_counters():
    page_cache.set("a.pdf", 1, 200, b"A")
    page_cache.get("a.pdf", 1, 200)  # 1 hit
    page_cache.get("a.pdf", 2, 200)  # 1 miss
    page_cache.reset()
    assert page_cache.stats() == {"hits": 0, "misses": 0, "hit_rate": 0.0, "size": 0}


# ---------------------------------------------------------------------------
# Integration: notes._render_pages_async consults the cache
# ---------------------------------------------------------------------------


def test_render_pages_async_uses_cache(monkeypatch):
    """Two sequential render calls for overlapping pages should hit the
    cache on the overlap and only invoke the underlying renderer for
    the new page."""
    from notes import agent as notes_agent

    calls: list[int] = []

    def fake_render_single(pdf_path: str, page_num: int, dpi: int = 200):
        calls.append(page_num)
        # Distinct bytes per page keep the assertions meaningful.
        return page_num, f"PNG:{pdf_path}:{page_num}:{dpi}".encode()

    monkeypatch.setattr(notes_agent, "_render_single_page", fake_render_single)

    async def _run():
        first = await notes_agent._render_pages_async("x.pdf", [1, 2])
        second = await notes_agent._render_pages_async("x.pdf", [2, 3])
        return first, second

    first, second = asyncio.run(_run())

    # First call renders 1 and 2. Second call hits the cache for 2 and
    # only renders 3. Total underlying-render invocations: 3.
    assert sorted(calls) == [1, 2, 3]
    # Cached bytes are identical between the two calls for the shared
    # page 2 — critical invariant: vision does not see different bytes
    # for "the same page" within a run.
    assert first[2] == second[2]


# ---------------------------------------------------------------------------
# Env-var parsing (peer-review HIGH): bad XBRL_PAGE_CACHE_MAX must not
# crash module import — log and fall back instead.
# ---------------------------------------------------------------------------

def test_parse_max_entries_env_accepts_integer(monkeypatch):
    monkeypatch.setenv("XBRL_PAGE_CACHE_MAX", "128")
    assert page_cache._parse_max_entries_env(64) == 128


def test_parse_max_entries_env_falls_back_on_non_numeric(monkeypatch, caplog):
    monkeypatch.setenv("XBRL_PAGE_CACHE_MAX", "not-a-number")
    with caplog.at_level("WARNING", logger="tools.page_cache"):
        result = page_cache._parse_max_entries_env(64)
    assert result == 64
    assert any("not an integer" in r.message for r in caplog.records)


def test_parse_max_entries_env_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("XBRL_PAGE_CACHE_MAX", raising=False)
    assert page_cache._parse_max_entries_env(64) == 64
