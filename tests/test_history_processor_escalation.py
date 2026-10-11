"""Pinning tests for token-aware history-processor escalation (Plan 2).

Guards the `_ctx` wrappers in `extraction/history_processors.py`: below the soft
token watermark they behave exactly like the conservative processors; once the
current conversation (the latest model response's request size) crosses it,
text compaction uses tighter thresholds. Pre-write images are never stripped.
The watermark is read from `XBRL_SOFT_COMPACT_TOKENS` at call time.
"""

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RequestUsage

from extraction.history_processors import (
    strip_stale_images_ctx,
    compact_old_text_results_ctx,
)


class _Usage:
    def __init__(self, total: int):
        self.total_tokens = total


class _Ctx:
    """Minimal stand-in for pydantic-ai RunContext: only `.usage` is read."""

    def __init__(self, total_tokens: int):
        self.usage = _Usage(total_tokens)


def _png(tag: bytes) -> BinaryContent:
    return BinaryContent(data=tag, media_type="image/png")


def _image_batch_msg(pages: list[int]) -> ModelRequest:
    content: list = []
    for p in pages:
        content.append(f"=== Page {p} ===")
        content.append(_png(f"img{p}".encode()))
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="view_pdf_pages",
                content=content,
                tool_call_id=f"call-{pages}",
            )
        ]
    )


def _response(request_tokens: int = 0) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content="thinking")],
        usage=RequestUsage(input_tokens=request_tokens),
    )


def _count_images(messages) -> int:
    n = 0
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, list):
                n += sum(1 for it in part.content if isinstance(it, BinaryContent))
    return n


# --- Image stripping ------------------------------------------------------


def _three_prewrite_batches():
    # Three image batches, NO write — the discovery phase.
    return [
        _image_batch_msg([1]),
        _response(),
        _image_batch_msg([2]),
        _response(),
        _image_batch_msg([3]),
    ]


def test_below_watermark_keeps_all_prewrite_images(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = _three_prewrite_batches()
    out = strip_stale_images_ctx(_Ctx(1000), msgs)
    # Non-aggressive: discovery-phase rule keeps every image (run_id=126 fix).
    assert _count_images(out) == 3


def test_over_watermark_keeps_all_prewrite_images(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = _three_prewrite_batches()
    out = strip_stale_images_ctx(_Ctx(70000), msgs)
    # scanned-thrash fix (2026-06-20): pre-write images are NEVER stripped,
    # even over the watermark. Stripping pages the agent hasn't yet committed
    # to the workbook makes it re-fetch them and never write (run_id=126 /
    # scanned-PDF thrash) — a worse failure than the token cost. Aggressive
    # trimming applies only AFTER the first successful write. Token pressure is
    # still relieved pre-write via text compaction (see the text-compaction
    # tests below), never by blinding the agent to its own pages.
    assert _count_images(out) == 3


def test_disabled_watermark_never_escalates(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "0")
    msgs = _three_prewrite_batches()
    out = strip_stale_images_ctx(_Ctx(10_000_000), msgs)
    assert _count_images(out) == 3


# --- Text compaction ------------------------------------------------------


def _old_large_text_history(request_tokens: int = 0):
    """An old verify_totals result (800 chars, 3 turns ago) + a fresher one.

    ``request_tokens`` is the size of the latest request, which drives the
    watermark."""
    old = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="verify_totals",
                content="IMBALANCE " * 80,  # ~800 chars
                tool_call_id="call-old",
            )
        ]
    )
    fresh = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="verify_totals",
                content="ok now",
                tool_call_id="call-fresh",
            )
        ]
    )
    # 3 model responses after the old result → turns_ago == 3.
    return [old, _response(), _response(), _response(request_tokens), fresh]


def _is_compacted(messages) -> bool:
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_call_id == "call-old"
                and isinstance(part.content, str)
                and part.content.startswith("[verify_totals result")
            ):
                return True
    return False


def test_below_watermark_does_not_compact_short_recent(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    out = compact_old_text_results_ctx(_Ctx(0), _old_large_text_history(1000))
    # 3 turns < 6 and 800 chars < 1500 → conservative thresholds skip it.
    assert not _is_compacted(out)


def test_over_watermark_compacts_with_aggressive_thresholds(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    out = compact_old_text_results_ctx(_Ctx(0), _old_large_text_history(70000))
    # 3 turns >= 2 and 800 chars >= 500 → aggressive thresholds compact it.
    assert _is_compacted(out)


def test_running_total_of_resent_tokens_does_not_escalate(monkeypatch):
    # Run 179: every request re-sends the whole history, so the running total
    # passed 60,000 by the third request of every healthy SOFP run. Only the
    # current conversation size may trigger escalation.
    monkeypatch.delenv("XBRL_SOFT_COMPACT_TOKENS", raising=False)
    out = compact_old_text_results_ctx(
        _Ctx(10_000_000), _old_large_text_history(67_000)
    )
    assert not _is_compacted(out)


# --- Page transcripts -----------------------------------------------------


def _page_text_read(call_id: str, pages: list[int]) -> list:
    body = "\n".join(f"=== Page {p} ===\n<table>{'<td>1,234</td>' * 60}</table>" for p in pages)
    return [
        ModelResponse(parts=[ToolCallPart(
            tool_name="read_page_text", args={"pages": pages}, tool_call_id=call_id,
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="read_page_text", content=body, tool_call_id=call_id,
        )]),
    ]


def _page_text_content(messages, call_id: str) -> str:
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id == call_id:
                    return part.content
    raise AssertionError(call_id)


def test_page_transcript_batches_do_not_displace_each_other(monkeypatch):
    # Run 179 (Nielsen SOFP): reading pages 25-33 replaced the pages 16-24
    # transcript with a placeholder, so the agent re-read both batches until
    # it ran out of steps. Different page batches must survive aggressive mode.
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = (
        _page_text_read("p1", list(range(16, 25)))
        + _page_text_read("p2", list(range(25, 34)))
        + _page_text_read("p3", [10, 11])
        + [_response(), _response(), _response(70000)]
    )
    out = compact_old_text_results_ctx(_Ctx(70000), msgs)
    assert out is msgs
    assert _page_text_content(out, "p1").startswith("=== Page 16 ===")


def test_truncated_later_read_does_not_remove_unreturned_pages(monkeypatch):
    # read_page_text returns at most ten pages; asking for 1-14 returns 1-10,
    # so the earlier 5-14 transcript still holds pages 11-14.
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    later = _page_text_read("p2", list(range(1, 11)))
    later[0] = ModelResponse(parts=[ToolCallPart(
        tool_name="read_page_text", args={"pages": list(range(1, 15))}, tool_call_id="p2",
    )])
    msgs = (
        _page_text_read("p1", list(range(5, 15))) + later
        + [_response(), _response(), _response(70000)]
    )
    out = compact_old_text_results_ctx(_Ctx(0), msgs)
    assert _page_text_content(out, "p1").startswith("=== Page 5 ===")


def test_malformed_page_request_does_not_stop_compaction(monkeypatch):
    # A rejected call (pages=5, not a list) stays in history with retry
    # feedback instead of a result; compaction must carry on.
    from pydantic_ai.messages import RetryPromptPart

    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = [
        ModelResponse(parts=[ToolCallPart(
            tool_name="read_page_text", args={"pages": 5}, tool_call_id="bad",
        )]),
        ModelRequest(parts=[RetryPromptPart(
            tool_name="read_page_text", content="pages must be a list", tool_call_id="bad",
        )]),
        *_page_text_read("p1", [16, 17]),
        *_page_text_read("p2", [16, 17]),
        _response(), _response(), _response(70000),
    ]
    out = compact_old_text_results_ctx(_Ctx(0), msgs)
    assert _page_text_content(out, "p1").startswith("[read_page_text result for page(s) 16, 17")


def test_page_transcript_compacts_once_same_pages_reread(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = (
        _page_text_read("p1", [16, 17])
        + _page_text_read("p2", [15, 16, 17, 18])
        + [_response(), _response(), _response(70000)]
    )
    out = compact_old_text_results_ctx(_Ctx(0), msgs)
    placeholder = _page_text_content(out, "p1")
    assert placeholder.startswith("[read_page_text result for page(s) 16, 17")
    assert "read again later" in placeholder
    assert _page_text_content(out, "p2").startswith("=== Page 15 ===")


# --- pydantic-ai ctx-detection contract -----------------------------------
#
# pydantic-ai 1.77 decides whether a history processor takes a RunContext purely
# from the FIRST PARAMETER'S TYPE ANNOTATION (`_utils.takes_run_context` →
# `get_first_param_type`). An un-annotated `ctx` makes it treat the 2-arg wrapper
# as a 1-arg `(messages)` processor and call it with a single positional, raising
# `strip_stale_images_ctx() missing 1 required positional argument: 'messages'`
# inside the live agent — a failure the positional unit tests above can't catch.


def test_ctx_wrappers_detected_as_run_context_taking():
    from pydantic_ai._utils import takes_run_context

    assert takes_run_context(strip_stale_images_ctx)
    assert takes_run_context(compact_old_text_results_ctx)
