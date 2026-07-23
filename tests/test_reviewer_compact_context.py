"""Plan agent-efficiency Phase 1A — reviewer image-history compaction.

Pins three contracts:

* ``strip_stale_reviewer_images`` keeps the newest TWO image-bearing view
  results verbatim (the face-page-vs-note-page cross-reference pattern) and
  replaces older page images with a text placeholder; the input list is
  never mutated (purity — the in-memory conversation and traces share these
  part objects).
* Flag OFF (the default) ⇒ ``create_reviewer_agent`` registers NO
  capabilities — the factory output is structurally identical to today.
* Flag ON ⇒ exactly one ProcessHistory capability carrying the reviewer
  stripper (never extraction's write-event-keyed one).
"""

import copy

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)

from correction.history_processors import (
    KEEP_RECENT_IMAGE_BATCHES,
    strip_stale_reviewer_images,
)


def _png(tag: bytes = b"img") -> BinaryContent:
    return BinaryContent(data=tag, media_type="image/png")


def _view_msg(pages: list[int]) -> ModelRequest:
    content: list = []
    for p in pages:
        content.append(f"=== Page {p} ===")
        content.append(_png(f"img{p}".encode()))
    return ModelRequest(parts=[ToolReturnPart(
        tool_name="view_pdf_pages", content=content,
        tool_call_id=f"call-view-{pages}")])


def _count_images(messages) -> int:
    n = 0
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolReturnPart) and isinstance(
                    part.content, list):
                n += sum(1 for it in part.content
                         if isinstance(it, BinaryContent))
    return n


def test_keeps_newest_two_batches_strips_older():
    messages = [
        _view_msg([3]),
        ModelResponse(parts=[TextPart("looked at 3")]),
        _view_msg([7, 8]),
        ModelResponse(parts=[TextPart("looked at 7-8")]),
        _view_msg([12]),
    ]
    before = copy.deepcopy(messages)
    out = strip_stale_reviewer_images(messages)

    # Oldest batch stripped; newest two kept (pages 7,8,12 = 3 images).
    assert _count_images(out) == 3
    stripped = out[0].parts[0].content
    assert "=== Page 3 ===" in stripped
    assert any(isinstance(i, str) and "viewed in full on an earlier turn" in i
               for i in stripped)
    # Purity: the input messages were not mutated.
    assert _count_images(messages) == _count_images(before) == 4


def test_two_or_fewer_batches_untouched():
    messages = [
        _view_msg([3]),
        ModelResponse(parts=[TextPart("t")]),
        _view_msg([7]),
    ]
    out = strip_stale_reviewer_images(messages)
    assert out is messages
    assert KEEP_RECENT_IMAGE_BATCHES == 2


def _build_agent(tmp_path):
    from correction.reviewer_agent import create_reviewer_agent
    from db.schema import init_db
    import sqlite3

    db = tmp_path / "cc.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    run_id = int(conn.execute(
        "INSERT INTO runs(created_at, pdf_filename, status) "
        "VALUES ('2026-07-23T00:00:00Z','x.pdf','running')").lastrowid)
    conn.commit()
    conn.close()
    agent, _deps = create_reviewer_agent(
        model="test", db_path=db, run_id=run_id)
    return agent


def _process_history_caps(agent):
    """The agent's ProcessHistory capabilities (user-registered processors).

    pydantic-ai 2.9 folds user capabilities into ``agent.root_capability``
    (a CombinedCapability) next to framework defaults like ToolSearch —
    filter by type so the assertion ignores those defaults.
    """
    from pydantic_ai.capabilities import ProcessHistory
    root = getattr(agent, "root_capability", None)
    caps = list(getattr(root, "capabilities", []) or [])
    return [c for c in caps if isinstance(c, ProcessHistory)]


def test_flag_off_registers_no_history_processors(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRL_REVIEWER_COMPACT_CONTEXT", raising=False)
    agent = _build_agent(tmp_path)
    assert _process_history_caps(agent) == [], (
        "flag off must leave the factory output identical to today — "
        "no history processors at all")


def test_flag_only_changes_capabilities_never_tools_or_prompt(
        tmp_path, monkeypatch):
    """Factory-identity depth: the flag's ONLY effect is the ProcessHistory
    capability — registered tool names and the system prompt are identical
    in both states."""
    def _tool_names(agent) -> set:
        names = set()
        for ts in agent.toolsets:
            tools = getattr(ts, "tools", {})
            if isinstance(tools, dict):
                names.update(tools.keys())
        return names

    monkeypatch.delenv("XBRL_REVIEWER_COMPACT_CONTEXT", raising=False)
    off = _build_agent(tmp_path)
    monkeypatch.setenv("XBRL_REVIEWER_COMPACT_CONTEXT", "1")
    on = _build_agent(tmp_path)
    assert _tool_names(off) == _tool_names(on)
    assert off._system_prompts == on._system_prompts


def test_flag_on_registers_reviewer_stripper(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_REVIEWER_COMPACT_CONTEXT", "1")
    agent = _build_agent(tmp_path)
    caps = _process_history_caps(agent)
    assert len(caps) == 1
    name = getattr(caps[0].processor, "__name__", repr(caps[0].processor))
    assert "strip_stale_reviewer_images" in str(name), (
        "must be the reviewer-shaped stripper, never extraction's")
