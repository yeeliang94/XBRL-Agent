"""Source-honesty (rewrite Phase 6.3): the notes-inventory build reports HOW
it was produced — deterministic regex ("text") vs LLM/OCR fallback ("vision")
vs nothing ("none") — and that label round-trips through the Infopack.

These pin `build_notes_inventory_with_source_async`'s source determination by
patching its internals (so no real PDF / vision model is needed) and the
Infopack serialisation of `inventory_source`.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from scout import notes_discoverer as nd
from scout.infopack import Infopack


def _run(coro):
    return asyncio.run(coro)


def test_source_is_text_when_regex_finds_headers(monkeypatch):
    monkeypatch.setattr(nd, "_resolve_vision_range", lambda *a, **k: ((5, 10), [object()]))
    monkeypatch.setattr(
        nd, "extract_inventory_from_pages",
        lambda pages: [nd.NoteInventoryEntry(note_num=1, title="PPE", page_range=(5, 5))],
    )
    entries, source = _run(nd.build_notes_inventory_with_source_async("p.pdf", 5))
    assert source == "text"
    assert len(entries) == 1


def test_source_is_none_when_regex_empty_and_no_vision(monkeypatch):
    monkeypatch.setattr(nd, "_resolve_vision_range", lambda *a, **k: ((5, 10), []))
    monkeypatch.setattr(nd, "extract_inventory_from_pages", lambda pages: [])
    entries, source = _run(nd.build_notes_inventory_with_source_async("p.pdf", 5))
    assert source == "none"
    assert entries == []


def test_source_is_vision_when_fallback_runs(monkeypatch):
    monkeypatch.setattr(nd, "_resolve_vision_range", lambda *a, **k: ((5, 10), []))
    monkeypatch.setattr(nd, "extract_inventory_from_pages", lambda pages: [])

    async def _fake_vision(**kwargs):
        return [nd.NoteInventoryEntry(note_num=2, title="From OCR", page_range=(5, 6))]

    import scout.notes_discoverer_vision as ndv
    monkeypatch.setattr(ndv, "_vision_inventory", _fake_vision)

    entries, source = _run(
        nd.build_notes_inventory_with_source_async("p.pdf", 5, vision_model=object())
    )
    assert source == "vision"
    assert len(entries) == 1


def test_source_is_vision_even_when_fallback_finds_nothing(monkeypatch):
    """The label records the METHOD, not the yield — a vision pass that returns
    empty is still 'vision' (cost incurred, OCR determinism involved)."""
    monkeypatch.setattr(nd, "_resolve_vision_range", lambda *a, **k: ((5, 10), []))
    monkeypatch.setattr(nd, "extract_inventory_from_pages", lambda pages: [])

    async def _fake_vision(**kwargs):
        return []

    import scout.notes_discoverer_vision as ndv
    monkeypatch.setattr(ndv, "_vision_inventory", _fake_vision)

    entries, source = _run(
        nd.build_notes_inventory_with_source_async("p.pdf", 5, vision_model=object())
    )
    assert source == "vision"
    assert entries == []


def test_legacy_list_only_async_builder_still_returns_a_list(monkeypatch):
    """build_notes_inventory_async must keep its list-only contract (its many
    callers/tests depend on it) by delegating to the with-source variant."""
    monkeypatch.setattr(nd, "_resolve_vision_range", lambda *a, **k: ((5, 10), [object()]))
    monkeypatch.setattr(
        nd, "extract_inventory_from_pages",
        lambda pages: [nd.NoteInventoryEntry(note_num=1, title="PPE", page_range=(5, 5))],
    )
    out = _run(nd.build_notes_inventory_async("p.pdf", 5))
    assert isinstance(out, list) and len(out) == 1


@pytest.mark.parametrize("source", ["text", "vision", "none", "unknown"])
def test_infopack_round_trips_inventory_source(source):
    ip = Infopack(toc_page=1, page_offset=0, inventory_source=source)
    assert Infopack.from_json(ip.to_json()).inventory_source == source


def test_infopack_defaults_and_coerces_inventory_source():
    # Missing key → default "unknown".
    assert Infopack.from_json(json.dumps({"toc_page": 1, "page_offset": 0})).inventory_source == "unknown"
    # Unknown/garbage value → coerced to "unknown", never crashes the load.
    bad = json.dumps({"toc_page": 1, "page_offset": 0, "inventory_source": "ocr-magic"})
    assert Infopack.from_json(bad).inventory_source == "unknown"
