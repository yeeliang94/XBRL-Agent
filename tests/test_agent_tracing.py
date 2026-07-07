"""Tests for conversation-trace persistence (agent_tracing).

Covers the v8 telemetry behaviour + peer-review [1]:
- text content is preserved verbatim (not elided at 500 bytes) so the trace
  viewer shows exactly what was sent/returned,
- oversized single payloads are capped,
- per-turn metrics ride alongside the messages,
- `save_messages_trace` writes a usable trace from a PARTIAL run's message
  history (the failed/timeout/iteration-capped agent case), and
- both writers are best-effort (never raise).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_tracing import (
    save_agent_trace,
    save_messages_trace,
    _MAX_TRACE_STR_CHARS,
)


class _Msg:
    """Minimal stand-in for a pydantic-ai message with model_dump."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _Result:
    """Stand-in for a finished AgentRunResult."""

    def __init__(self, messages: list) -> None:
        self._messages = messages

    def all_messages(self) -> list:
        return self._messages


def _read_trace(output_dir: Path, prefix: str) -> dict:
    return json.loads(
        (output_dir / f"{prefix}_conversation_trace.json").read_text(encoding="utf-8")
    )


def test_save_agent_trace_keeps_text_verbatim(tmp_path: Path) -> None:
    """A tool-result content string under the cap survives verbatim — the
    legacy 500-byte elision would have hidden it."""
    long_text = "x" * 2000  # well over the old 500-byte strip threshold
    result = _Result([_Msg({"role": "tool", "content": long_text})])
    save_agent_trace(result, str(tmp_path), "SOFP")

    trace = _read_trace(tmp_path, "SOFP")
    assert trace["messages"][0]["content"] == long_text


def test_save_agent_trace_caps_oversized_payload(tmp_path: Path) -> None:
    """A single huge payload is truncated with a marker (full-verbatim with
    a per-cell cap)."""
    huge = "y" * (_MAX_TRACE_STR_CHARS + 5000)
    result = _Result([_Msg({"role": "user", "content": huge})])
    save_agent_trace(result, str(tmp_path), "SOPL")

    content = _read_trace(tmp_path, "SOPL")["messages"][0]["content"]
    assert len(content) < len(huge)
    assert "truncated" in content


def test_save_agent_trace_strips_binary(tmp_path: Path) -> None:
    """Raw bytes are elided (image payloads) regardless of size."""
    result = _Result([_Msg({"role": "user", "data": b"\x89PNG\r\n" * 100})])
    save_agent_trace(result, str(tmp_path), "SOCF")
    data = _read_trace(tmp_path, "SOCF")["messages"][0]["data"]
    assert "bytes stripped" in data


def test_save_agent_trace_includes_turns(tmp_path: Path) -> None:
    """Per-turn metrics ride alongside the messages; the coordinator-internal
    `_n_tool_calls` helper key is stripped."""
    result = _Result([_Msg({"role": "user", "content": "hi"})])
    turns = [
        {"turn_index": 1, "node_kind": "model_request", "prompt_tokens": 10,
         "_n_tool_calls": 0},
    ]
    save_agent_trace(result, str(tmp_path), "SOCI", turns=turns)
    trace = _read_trace(tmp_path, "SOCI")
    assert trace["turns"][0]["turn_index"] == 1
    assert "_n_tool_calls" not in trace["turns"][0]


def test_save_messages_trace_writes_partial_run(tmp_path: Path) -> None:
    """Peer-review [1]: a failed/timeout agent has no final result, but its
    accumulated message history must still produce a debuggable trace."""
    messages = [
        _Msg({"role": "system", "content": "system prompt"}),
        _Msg({"role": "user", "content": "extract SOFP"}),
        _Msg({"role": "assistant", "content": "calling tool..."}),
    ]
    turns = [{"turn_index": 1, "node_kind": "call_tools", "tool_names": "read_template"}]
    save_messages_trace(messages, str(tmp_path), "SOFP", turns=turns)

    trace = _read_trace(tmp_path, "SOFP")
    assert len(trace["messages"]) == 3
    assert trace["messages"][0]["content"] == "system prompt"
    assert trace["turns"][0]["tool_names"] == "read_template"


def test_writers_are_best_effort(tmp_path: Path) -> None:
    """A broken result/messages object must not raise — telemetry is
    advisory and can never fault a run."""
    class _Boom:
        def all_messages(self):
            raise RuntimeError("boom")

    # Neither call should raise.
    save_agent_trace(_Boom(), str(tmp_path), "SOFP")
    save_messages_trace(object(), str(tmp_path), "SOPL")  # not iterable → caught
    # No trace files should have been written for the broken inputs.
    assert not (tmp_path / "SOFP_conversation_trace.json").exists()


def test_prefix_with_windows_forbidden_chars_still_writes(tmp_path: Path) -> None:
    """A prefix carrying Windows-forbidden characters (live sub-agent ids are
    shaped "notes:LIST_OF_NOTES:sub0") must be sanitized, not passed through:
    the save_* wrappers swallow errors, so an invalid filename would drop the
    trace SILENTLY on Windows — the exact platform run-63 made diagnosable."""
    messages = [_Msg({"role": "tool", "content": "hello"})]
    save_messages_trace(
        messages, str(tmp_path), 'NOTES_LIST_OF_NOTES_notes:LIST<>OF|NOTES?:sub0'
    )

    written = list(tmp_path.glob("*_conversation_trace.json"))
    assert len(written) == 1
    name = written[0].name
    for ch in '<>:"/\\|?*':
        assert ch not in name
    trace = json.loads(written[0].read_text(encoding="utf-8"))
    assert trace["messages"][0]["content"] == "hello"


def test_trace_note_stamped_on_every_trace(tmp_path: Path) -> None:
    """Every trace file carries the compaction disclaimer (run-63 fix): the
    persisted history is the END-STATE after token-saving processors, so a
    reader must not infer per-turn visibility from placeholders."""
    save_messages_trace([_Msg({"role": "user", "content": "hi"})], str(tmp_path), "SOFP")
    trace = _read_trace(tmp_path, "SOFP")
    assert "compaction" in trace["trace_note"]
    assert "Do not infer" in trace["trace_note"]
