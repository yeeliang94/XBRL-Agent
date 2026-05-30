"""Peer-review F5: discover_notes_inventory's tool RETURN must carry subnotes.

The scout echoes the tool return into its save_infopack JSON. _save_infopack_impl
rebuilds the inventory from that echo and only falls back to deps.notes_inventory
when the echoed list is entirely empty — so a subnote-less return silently drops
the sub-note hierarchy the scout-coverage work added.
"""
from __future__ import annotations

import asyncio

import pytest

from scout.agent import ScoutDeps, _discover_notes_inventory_impl
from scout.notes_discoverer import NoteInventoryEntry, SubNoteInventoryEntry


def _deps() -> ScoutDeps:
    return ScoutDeps(
        pdf_path="/dev/null",
        pdf_length=100,
        statements_to_find=None,
        on_progress=None,
    )


def test_tool_return_includes_subnotes(monkeypatch):
    entry = NoteInventoryEntry(
        note_num=2,
        title="Summary of material accounting policies",
        page_range=(18, 24),
        subnotes=[
            SubNoteInventoryEntry(subnote_ref="2.1", title="Basis", page_range=(18, 18)),
            SubNoteInventoryEntry(subnote_ref="2.14", title="Employee benefits", page_range=(23, 24)),
        ],
    )

    async def _fake_build(**kwargs):
        # Phase 6.3: the impl now calls the with-source variant, which returns
        # (entries, source).
        return [entry], "text"

    monkeypatch.setattr(
        "scout.notes_discoverer.build_notes_inventory_with_source_async", _fake_build
    )
    deps = _deps()
    out = asyncio.run(_discover_notes_inventory_impl(deps, notes_start_page=18))
    assert len(out) == 1
    assert out[0]["note_num"] == 2
    subs = out[0].get("subnotes")
    assert isinstance(subs, list) and len(subs) == 2
    assert subs[0] == {"subnote_ref": "2.1", "title": "Basis", "page_range": [18, 18]}
    assert subs[1]["subnote_ref"] == "2.14"
    # Phase 6.3: the build source is recorded on deps.
    assert deps.inventory_source == "text"


def test_tool_return_empty_subnotes_when_none(monkeypatch):
    entry = NoteInventoryEntry(note_num=3, title="PPE", page_range=(25, 26))

    async def _fake_build(**kwargs):
        return [entry], "vision"

    monkeypatch.setattr(
        "scout.notes_discoverer.build_notes_inventory_with_source_async", _fake_build
    )
    deps = _deps()
    out = asyncio.run(_discover_notes_inventory_impl(deps, notes_start_page=25))
    assert out[0]["subnotes"] == []
    assert deps.inventory_source == "vision"
