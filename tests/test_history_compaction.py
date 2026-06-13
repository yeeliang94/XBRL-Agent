"""Unit tests for the stale-text-result compaction processor (item 30).

These pin the four compaction rules — old-enough, large-enough, keep-newest-
per-tool, never-touch-template/images — plus the purity contract shared with
the other history processors. See extraction/history_processors.py and
docs/PLAN-orchestration-hardening.html item 30.
"""

import copy

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)

from extraction.history_processors import (
    COMPACT_AFTER_TURNS,
    COMPACT_MIN_CHARS,
    compact_old_text_results,
)

# A payload comfortably over the size threshold, distinct per call so we can
# tell which result survived verbatim.
_BIG = "imbalance detail line; " * 120  # ~2.6k chars


def _tool_msg(tool_name: str, content, call_id: str = "") -> ModelRequest:
    """A ModelRequest carrying one tool return."""
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id=call_id or f"call-{tool_name}",
            )
        ]
    )


def _response(text: str = "thinking") -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def _png() -> BinaryContent:
    return BinaryContent(data=b"img", media_type="image/png")


def _content_text(part: ToolReturnPart) -> str:
    c = part.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x for x in c if isinstance(x, str))
    return ""


def _first_tool_part(messages) -> ToolReturnPart:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    return part
    raise AssertionError("no tool return part found")


def _trailing_responses(n: int) -> list:
    return [_response(f"turn {i}") for i in range(n)]


def test_old_large_result_is_compacted():
    """A big result with > COMPACT_AFTER_TURNS responses after it is summarised,
    while a newer result of the same tool stays verbatim."""
    messages = [
        _tool_msg("verify_totals", _BIG, "old"),
        *_trailing_responses(COMPACT_AFTER_TURNS),  # enough turns elapse
        _tool_msg("verify_totals", "fresh small result", "new"),
    ]

    out = compact_old_text_results(messages)

    old_part = out[0].parts[0]
    assert "compacted to save tokens" in _content_text(old_part)
    assert len(_content_text(old_part)) < COMPACT_MIN_CHARS
    # The fresh result is untouched.
    new_part = out[-1].parts[0]
    assert _content_text(new_part) == "fresh small result"


def test_most_recent_result_per_tool_kept_verbatim():
    """Even when old AND large, the newest result of a tool is never compacted —
    it's the agent's current working state."""
    messages = [
        _tool_msg("verify_totals", _BIG, "only"),
        *_trailing_responses(COMPACT_AFTER_TURNS + 2),
    ]

    out = compact_old_text_results(messages)

    # Only result of its tool → kept verbatim despite age + size.
    assert _content_text(out[0].parts[0]) == _BIG
    assert out is messages  # nothing changed → same object (purity fast-path)


def test_recent_result_not_compacted():
    """A big result that is NOT yet old enough is left alone."""
    messages = [
        _tool_msg("verify_totals", _BIG, "recent"),
        *_trailing_responses(COMPACT_AFTER_TURNS - 1),  # one short of the bound
        _tool_msg("verify_totals", "newer", "newer"),
    ]

    out = compact_old_text_results(messages)

    assert _content_text(out[0].parts[0]) == _BIG


def test_small_old_result_not_compacted():
    """An old result below the size threshold isn't worth compacting."""
    small = "imbalance RM 10"
    messages = [
        _tool_msg("verify_totals", small, "old"),
        *_trailing_responses(COMPACT_AFTER_TURNS),
        _tool_msg("verify_totals", "newer", "newer"),
    ]

    out = compact_old_text_results(messages)

    assert _content_text(out[0].parts[0]) == small


def test_template_summary_never_compacted():
    """read_template output is referenced repeatedly — never compact it, even
    when old and large."""
    template = "=== Sheet: SOFP ===\n" + ("row detail\n" * 400)
    assert len(template) >= COMPACT_MIN_CHARS
    messages = [
        _tool_msg("read_template", template, "tmpl"),
        *_trailing_responses(COMPACT_AFTER_TURNS + 1),
        _tool_msg("read_template", "later read", "tmpl2"),
    ]

    out = compact_old_text_results(messages)

    assert _content_text(out[0].parts[0]) == template


def test_write_confirmations_never_compacted():
    """A write tool's confirmation is the agent's only record of WHAT was
    already written — compacting an old one risks re-write loops. Exempt the
    same way read_template summaries are (code-review fix, 2026-06-13)."""
    confirmation = "Successfully wrote 42 fields.\n" + ("row receipt\n" * 200)
    assert len(confirmation) >= COMPACT_MIN_CHARS
    for tool in ("write_facts", "fill_workbook", "write_notes"):
        messages = [
            _tool_msg(tool, confirmation, "w1"),
            *_trailing_responses(COMPACT_AFTER_TURNS + 2),
            _tool_msg(tool, "Successfully wrote 3 fields.", "w2"),
        ]

        out = compact_old_text_results(messages)

        assert _content_text(out[0].parts[0]) == confirmation, tool


def test_image_batches_never_compacted_here():
    """Image-carrying returns belong to strip_stale_images; compaction skips
    them so the two processors don't fight over the same parts."""
    big_marker = "=== Page 5 ===" + " padding" * 300  # large text + an image
    content = [big_marker, _png()]
    messages = [
        _tool_msg("view_pdf_pages", content, "img"),
        *_trailing_responses(COMPACT_AFTER_TURNS + 1),
        _tool_msg("view_pdf_pages", [_png()], "img2"),
    ]

    out = compact_old_text_results(messages)

    # The old image batch's text/image is untouched by THIS processor.
    assert out[0].parts[0].content is content


def test_purity_input_never_mutated():
    """The input message list and its parts are never mutated in place."""
    messages = [
        _tool_msg("verify_totals", _BIG, "old"),
        *_trailing_responses(COMPACT_AFTER_TURNS),
        _tool_msg("verify_totals", "fresh", "new"),
    ]
    snapshot = copy.deepcopy(messages)

    compact_old_text_results(messages)

    # Original objects unchanged (deep equality on the snapshot).
    assert _content_text(_first_tool_part(messages)) == _content_text(
        _first_tool_part(snapshot)
    )
    assert _content_text(messages[0].parts[0]) == _BIG


def test_summary_preserves_breadcrumb():
    """The one-line summary keeps tool name, size, and the payload's first line
    so the agent can still tell what the superseded call was."""
    payload = "FIRST LINE MARKER\n" + ("body line\n" * 200)
    messages = [
        _tool_msg("verify_totals", payload, "old"),
        *_trailing_responses(COMPACT_AFTER_TURNS),
        _tool_msg("verify_totals", "fresh", "new"),
    ]

    out = compact_old_text_results(messages)
    summary = _content_text(out[0].parts[0])

    assert "verify_totals" in summary
    assert "FIRST LINE MARKER" in summary
    assert str(len(payload)) in summary
