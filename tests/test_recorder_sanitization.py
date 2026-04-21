"""Peer-review I-4: ANSI / C0 control-char sanitisation in SSEEventRecorder.

LLM-authored strings (tool results, chosen_row_label, evidence, content)
land in SSE payloads and get persisted verbatim to SQLite + side-logs.
If the model emits terminal escape sequences (\\x1b[31m to color output
red, for instance), operators tailing the server log see their terminal
reshaped. React auto-escapes in the UI, so XSS isn't exploitable today —
this is defence-in-depth for operator tooling.

These tests pin the sanitiser's contract:
  - ANSI CSI sequences (colors, cursor moves) are stripped entirely.
  - ANSI OSC sequences (title, hyperlinks) are stripped entirely.
  - Standalone C0 controls (NUL, BEL, form-feed) are stripped.
  - Tab, newline, carriage-return are PRESERVED — they're load-bearing
    in multi-line notes content.
  - Nested structures (lists, dicts) are sanitised recursively.
  - Non-strings (ints, None, bools) pass through untouched.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from db.recorder import SSEEventRecorder, _sanitize, _sanitize_str


def test_strips_ansi_color_csi_sequences():
    # Classic SGR red → strip everything between ESC[ and m.
    assert _sanitize_str("\x1b[31mred\x1b[0m text") == "red text"


def test_strips_ansi_cursor_movement():
    # CSI with numeric params + a letter command (cursor up, clear screen).
    assert _sanitize_str("before\x1b[2Jafter") == "beforeafter"
    assert _sanitize_str("up\x1b[1;5Hdown") == "updown"


def test_strips_osc_title_setting():
    # Operating System Command — "set terminal title", terminated by BEL.
    assert _sanitize_str("before\x1b]0;title\x07after") == "beforeafter"


def test_strips_osc_hyperlink():
    # OSC 8 hyperlink: ESC ] 8 ; ; URL BEL text ESC ] 8 ; ; BEL.
    # Both OSC segments get removed.
    s = "click \x1b]8;;http://evil\x07here\x1b]8;;\x07 end"
    assert _sanitize_str(s) == "click here end"


def test_preserves_tab_newline_carriage_return():
    # These are load-bearing in multi-line notes content (paragraph
    # breaks + ASCII-aligned tables) and render harmlessly in logs.
    assert _sanitize_str("line1\nline2\tcol\rtrailing") == "line1\nline2\tcol\rtrailing"


def test_strips_bell_and_nul_and_form_feed():
    assert _sanitize_str("ring\x07bell") == "ringbell"
    assert _sanitize_str("null\x00here") == "nullhere"
    assert _sanitize_str("form\x0cfeed") == "formfeed"


def test_strips_delete_char():
    # 0x7f (DEL) is also a C0 control — strip it.
    assert _sanitize_str("before\x7fafter") == "beforeafter"


def test_leaves_plain_ascii_untouched():
    assert _sanitize_str("hello, world!") == "hello, world!"


def test_leaves_unicode_untouched():
    # Unicode accented / CJK / emoji content is not a terminal hazard.
    assert _sanitize_str("café 日本語 🎉") == "café 日本語 🎉"


def test_sanitize_handles_nested_dict():
    payload = {
        "tool_name": "write_notes",
        "args": {
            "content": "\x1b[31malert\x1b[0m — normal",
            "evidence": "p. 27\x07",
        },
    }
    cleaned = _sanitize(payload)
    assert cleaned["args"]["content"] == "alert — normal"
    assert cleaned["args"]["evidence"] == "p. 27"


def test_sanitize_handles_list_of_strings():
    payload = {"pages": ["Page \x1b[1m27\x1b[0m", "Page 28\x07"]}
    cleaned = _sanitize(payload)
    assert cleaned["pages"] == ["Page 27", "Page 28"]


def test_sanitize_passes_through_non_strings():
    # Ints / None / bools must not be mutated.
    payload = {"count": 42, "missing": None, "flag": True, "ratio": 1.5}
    assert _sanitize(payload) == payload


def test_end_to_end_persisted_payload_is_sanitised(tmp_path: Path):
    """Record an event with an ANSI escape in the payload and verify the
    row written to SQLite is clean. This is the contract that actually
    matters: a log-tailing operator reading the DB must not see raw
    escape sequences."""
    rec = SSEEventRecorder(tmp_path / "audit.db", pdf_filename="x.pdf")
    rec.start()
    assert rec.run_agent_id is not None

    rec.record({
        "event": "tool_result",
        "data": {
            "tool_name": "view_pdf_pages",
            "result_summary": "\x1b[31mError\x1b[0m: could not render",
        },
    })

    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    try:
        rows = conn.execute(
            "SELECT payload_json FROM agent_events WHERE run_agent_id = ?",
            (rec.run_agent_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    # The ANSI markers must be gone from what's persisted.
    assert "\x1b" not in rows[0][0]
    assert payload["result_summary"] == "Error: could not render"
