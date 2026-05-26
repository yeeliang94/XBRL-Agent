"""Unit tests for the token-cost history processors.

These pin the purity contract (input list never mutated) and the
last-batch-only retention rule for images, plus the keep-first-only rule for
duplicate template summaries. See docs/PLAN-token-cost-reduction.md.
"""

import copy

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from extraction.history_processors import (
    strip_stale_images,
    strip_duplicate_template,
)


def _png(tag: bytes = b"img") -> BinaryContent:
    return BinaryContent(data=tag, media_type="image/png")


def _image_batch_msg(tool_name: str, pages: list[int]) -> ModelRequest:
    """A ModelRequest carrying one image tool return for the given pages."""
    content: list = []
    for p in pages:
        content.append(f"=== Page {p} ===")
        content.append(_png(f"img{p}".encode()))
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id=f"call-{tool_name}-{pages}",
            )
        ]
    )


def _write_msg(tool_name: str = "fill_workbook") -> ModelRequest:
    """A ModelRequest carrying a successful write tool return."""
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content="Successfully wrote 5 fields to /tmp/out.xlsx.",
                tool_call_id=f"call-{tool_name}",
            )
        ]
    )


def _failed_write_msg(tool_name: str, content: str) -> ModelRequest:
    """A ModelRequest carrying a FAILED write tool return (error string)."""
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name,
                content=content,
                tool_call_id=f"call-{tool_name}-fail",
            )
        ]
    )


def _count_images(messages) -> int:
    n = 0
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and isinstance(part.content, list):
                    n += sum(1 for it in part.content if isinstance(it, BinaryContent))
    return n


def test_strip_stale_images_keeps_all_before_first_write():
    """run_id=126 regression: before any write, the agent needs multiple pages
    visible at once, so NOTHING is stripped — this is what caused the loop."""
    messages = [
        _image_batch_msg("view_pdf_pages", [16, 17, 18, 19, 20]),
        ModelResponse(parts=[TextPart("reading")]),
        _image_batch_msg("view_pdf_pages", [21]),
    ]

    out = strip_stale_images(messages)

    # No write yet → all 6 images retained.
    assert out is messages
    assert _count_images(out) == 6


def test_strip_stale_images_strips_pre_write_batches_after_write():
    """Once a write lands, batches that PRECEDE it are stripped (their data is
    captured in the workbook); the newest batch is always kept."""
    messages = [
        _image_batch_msg("view_pdf_pages", [1, 2]),       # pre-write → strip
        ModelResponse(parts=[TextPart("looking")]),
        _image_batch_msg("view_pdf_pages", [3]),          # pre-write → strip
        _write_msg("fill_workbook"),                       # write boundary
        _image_batch_msg("view_pdf_pages", [4, 5]),       # post-write → keep
    ]

    out = strip_stale_images(messages)

    # Pre-write batches stripped (pages 1,2,3 → 3 images gone); post-write
    # batch (4,5) kept → 2 images remain.
    assert _count_images(out) == 2

    first_return = out[0].parts[0]
    placeholders = [it for it in first_return.content if isinstance(it, str)]
    # Wording discourages re-fetching rather than inviting it.
    assert any("do not re-open" in s for s in placeholders)
    assert any("Page 1" in s for s in placeholders)
    # Page markers preserved.
    assert any(s == "=== Page 1 ===" for s in first_return.content)


def test_strip_stale_images_keeps_newest_batch_even_if_pre_write():
    """If the only images all predate the last write, the newest batch is still
    kept so the agent is never fully blinded."""
    messages = [
        _image_batch_msg("view_pdf_pages", [1]),   # older → strip
        _image_batch_msg("view_pdf_pages", [2]),   # newest → keep
        _write_msg("fill_workbook"),
    ]
    out = strip_stale_images(messages)
    assert _count_images(out) == 1
    # Page 2 (newest) survives; page 1 stripped.
    assert any(
        isinstance(it, BinaryContent) for it in out[1].parts[0].content
    )


def test_strip_stale_images_is_pure():
    messages = [
        _image_batch_msg("view_pdf_pages", [1]),
        _image_batch_msg("view_pdf_pages", [2]),
        _write_msg("fill_workbook"),
    ]
    snapshot = copy.deepcopy(messages)

    strip_stale_images(messages)

    # Original input list and its parts are unchanged (no in-place mutation).
    assert _count_images(messages) == _count_images(snapshot) == 2
    assert messages[0].parts[0].content == snapshot[0].parts[0].content


def test_strip_stale_images_generic_over_tool_name():
    # Scout uses view_pages + fills via... scout has no write, but the strip
    # still triggers off a write tool return if present. Here notes' write_notes
    # is the boundary and view_pages images predate it.
    messages = [
        _image_batch_msg("view_pages", [1]),
        _image_batch_msg("view_pages", [2]),
        _write_msg("write_notes"),
    ]
    out = strip_stale_images(messages)
    assert _count_images(out) == 1  # newest (page 2) kept, page 1 stripped


def test_failed_write_is_not_a_strip_boundary():
    """run_id=126 class regression: a FAILED write (no data committed) must not
    flip the agent into post-write trimming. The agent still needs the earlier
    source pages to retry, so nothing is stripped."""
    for tool, err in (
        ("fill_workbook", "Failed to fill workbook. Errors: ['bad row']"),
        ("write_notes", "Invalid JSON: Expecting value: line 1 column 1"),
        ("write_notes", 'Expected a list of payloads or {"payloads": [...]}'),
    ):
        messages = [
            _image_batch_msg("view_pdf_pages", [1, 2]),
            _image_batch_msg("view_pdf_pages", [3]),
            _failed_write_msg(tool, err),
        ]
        out = strip_stale_images(messages)
        # No successful write → all 3 images retained, list untouched.
        assert out is messages, tool
        assert _count_images(out) == 3, (tool, err)


def test_notes_zero_row_write_is_not_a_boundary():
    """notes/agent.py emits "Wrote 0 row(s) … Writer errors: …" on the FAILURE
    path (the prefix is unconditional), and the sub-agent emits "Collected 0
    payload(s) … Rejected …". Neither committed data, so neither is a boundary
    — the agent keeps its source pages to retry."""
    for content in (
        "Wrote 0 row(s) to NOTES_5.\nWriter errors: row 'Revenue' not found",
        "Collected 0 payload(s) for sub-coordinator.\nRejected 2 payload(s) "
        "(label not in template):",
    ):
        messages = [
            _image_batch_msg("view_pdf_pages", [1, 2]),
            _image_batch_msg("view_pdf_pages", [3]),
            _failed_write_msg("write_notes", content),
        ]
        out = strip_stale_images(messages)
        assert out is messages, content
        assert _count_images(out) == 3, content


def test_notes_partial_write_is_a_boundary():
    """A partial notes write that committed >= 1 row IS a boundary — those rows
    really landed, so pre-write pages can trim even with skipped rows present."""
    messages = [
        _image_batch_msg("view_pdf_pages", [1, 2]),
        _failed_write_msg(
            "write_notes",
            "Wrote 3 row(s) to NOTES_5.\nWriter errors: 1 row skipped",
        ),
        _image_batch_msg("view_pdf_pages", [4, 5]),
    ]
    out = strip_stale_images(messages)
    assert _count_images(out) == 2  # pages 1,2 stripped; 4,5 kept


def test_successful_write_after_failed_write_strips_pre_write_batches():
    """Once a write actually succeeds, pre-write batches strip as before — the
    failed attempt in between doesn't change the boundary."""
    messages = [
        _image_batch_msg("view_pdf_pages", [1, 2]),                 # strip
        _failed_write_msg("fill_workbook", "Failed to fill workbook. Errors: []"),
        _image_batch_msg("view_pdf_pages", [3]),                    # strip (pre last write)
        _write_msg("fill_workbook"),                                 # real boundary
        _image_batch_msg("view_pdf_pages", [4, 5]),                 # keep (post-write)
    ]
    out = strip_stale_images(messages)
    # Pages 1,2,3 stripped; 4,5 kept.
    assert _count_images(out) == 2


def test_strip_stale_images_noop_with_single_batch():
    messages = [_image_batch_msg("view_pdf_pages", [1, 2])]
    out = strip_stale_images(messages)
    assert out is messages  # untouched
    assert _count_images(out) == 2


def _template_summary_msg(call_id: str) -> ModelRequest:
    summary = (
        "\n=== Sheet: SOFP ===\nTotal cells: 50 | Data entry: 30 | Formulas: 20\n"
        "  B3 (row 3): Cash [DATA_ENTRY]"
    )
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read_template",
                content=summary,
                tool_call_id=call_id,
            )
        ]
    )


def test_strip_duplicate_template_keeps_first_only():
    messages = [
        _template_summary_msg("c1"),
        ModelResponse(parts=[TextPart("ok")]),
        _template_summary_msg("c2"),
        _template_summary_msg("c3"),
    ]
    snapshot = copy.deepcopy(messages)

    out = strip_duplicate_template(messages)

    # First copy intact.
    assert "=== Sheet: SOFP ===" in out[0].parts[0].content
    # Later copies replaced with a pointer.
    assert out[2].parts[0].content == "Template structure already provided above."
    assert out[3].parts[0].content == "Template structure already provided above."

    # Purity.
    assert messages[2].parts[0].content == snapshot[2].parts[0].content


def test_strip_duplicate_template_noop_single():
    messages = [_template_summary_msg("c1")]
    out = strip_duplicate_template(messages)
    assert out is messages
