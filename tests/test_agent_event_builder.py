"""Characterization tests for the shared SSE event builder/emitter.

Pins ``agent_runner.build_agent_event`` / ``make_emitter`` to the EXACT dict
shapes the coordinators emitted before unification (rewrite Phase 2 follow-up,
PLAN-orchestration-seams Part A / Phase A1). The contract is shape-equivalence
— including the Sheet-12 sub-coordinator's ``sub_agent_id`` payload and parent
``agent_id`` override — not a single-definition grep.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runner import build_agent_event, make_emitter


# --- The prior, hand-rolled forms (verbatim copies of the pre-refactor code) ---

def _prior_plain(event_type, agent_id, agent_role, data):
    """coordinator._build_event + notes wrappers (585 / 1031)."""
    return {
        "event": event_type,
        "data": {**data, "agent_id": agent_id, "agent_role": agent_role},
    }


def _prior_sheet12(event_type, data, parent_agent_id, agent_role, sub_agent_id):
    """listofnotes_subcoordinator._emit (640) — parent id + sub_agent_id."""
    return {
        "event": event_type,
        "data": {
            **data,
            "agent_id": parent_agent_id,
            "agent_role": agent_role,
            "sub_agent_id": sub_agent_id,
        },
    }


@pytest.mark.parametrize("event_type,data", [
    ("status", {"phase": "started", "message": "Starting SOFP..."}),
    ("tool_call", {"tool_name": "write_facts", "tool_call_id": "abc", "args": {"x": 1}}),
    ("token_update", {"prompt_tokens": 10, "completion_tokens": 5, "cumulative": 15}),
    ("complete", {"success": True, "workbook_path": "/tmp/x.xlsx", "flag": None}),
    ("error", {"message": "boom"}),
])
def test_build_agent_event_matches_plain_form(event_type, data):
    got = build_agent_event(event_type, "sofp", "SOFP", data)
    assert got == _prior_plain(event_type, "sofp", "SOFP", data)


@pytest.mark.parametrize("event_type,data", [
    ("status", {"phase": "viewing_pdf", "message": "sub0: viewing pdf"}),
    ("tool_call", {"tool_name": "write_notes", "tool_call_id": "sub0:t1", "args": {}}),
    ("tool_result", {"tool_name": "write_notes", "tool_call_id": "sub0:t1",
                     "result_summary": "ok", "duration_ms": 42}),
    ("thinking_delta", {"content": "hmm", "thinking_id": "sub0_think_0"}),
    ("text_delta", {"content": "hello"}),
    ("token_update", {"prompt_tokens": 3, "completion_tokens": 2, "cumulative": 5}),
])
def test_build_agent_event_matches_sheet12_form(event_type, data):
    parent = "notes:LIST_OF_NOTES"
    role = "LIST_OF_NOTES"
    sub = "sub0"
    got = build_agent_event(
        event_type, parent, role, data, extra={"sub_agent_id": sub},
    )
    assert got == _prior_sheet12(event_type, data, parent, role, sub)
    # sub_agent_id present and the parent id wins (Notes-12 aggregation).
    assert got["data"]["sub_agent_id"] == sub
    assert got["data"]["agent_id"] == parent


def test_make_emitter_pushes_built_event_onto_queue():
    q: asyncio.Queue = asyncio.Queue()
    emit, _ = make_emitter(q, "sopl", "SOPL")

    async def run():
        await emit("status", {"phase": "started", "message": "go"})
        return q.get_nowait()

    got = asyncio.run(run())
    assert got == _prior_plain("status", "sopl", "SOPL",
                               {"phase": "started", "message": "go"})


def test_make_emitter_noop_without_queue():
    emit, safe_emit = make_emitter(None, "sopl", "SOPL")

    async def run():
        await emit("status", {"x": 1})
        await safe_emit("complete", {"success": False})

    asyncio.run(run())  # must not raise


def test_make_emitter_sheet12_extra_rides_payload():
    q: asyncio.Queue = asyncio.Queue()
    emit, _ = make_emitter(
        q, "notes:LIST_OF_NOTES", "LIST_OF_NOTES",
        extra={"sub_agent_id": "sub2"},
    )

    async def run():
        await emit("tool_call", {"tool_name": "view_pdf_pages",
                                 "tool_call_id": "sub2:t9", "args": {}})
        return q.get_nowait()

    got = asyncio.run(run())
    assert got["data"]["sub_agent_id"] == "sub2"
    assert got["data"]["agent_id"] == "notes:LIST_OF_NOTES"


def test_face_safe_emit_swallows_cancelled_only():
    """Face contract: safe_emit swallows CancelledError, NOT generic
    Exception (a genuine bug would still surface)."""
    class _BoomQueue:
        async def put(self, _):
            raise asyncio.CancelledError()

    _, safe_emit = make_emitter(_BoomQueue(), "sofp", "SOFP")

    async def run():
        await safe_emit("complete", {"success": False})  # swallowed

    asyncio.run(run())  # no raise

    class _ErrQueue:
        async def put(self, _):
            raise RuntimeError("real error")

    _, safe_emit_err = make_emitter(_ErrQueue(), "sofp", "SOFP")

    async def run_err():
        await safe_emit_err("complete", {"success": False})

    with pytest.raises(RuntimeError):
        asyncio.run(run_err())


def test_notes_safe_emit_swallows_exception():
    """Notes contract: safe_emit swallows Exception (the broad teardown
    catch the notes coordinators use)."""
    class _ErrQueue:
        async def put(self, _):
            raise RuntimeError("transient teardown error")

    _, safe_emit = make_emitter(
        _ErrQueue(), "CORPORATE_INFO", "CORPORATE_INFO",
        safe_swallow=(Exception,),
    )

    async def run():
        await safe_emit("complete", {"success": False})  # swallowed

    asyncio.run(run())  # no raise
