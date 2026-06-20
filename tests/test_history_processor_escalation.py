"""Pinning tests for token-aware history-processor escalation (Plan 2).

Guards the `_ctx` wrappers in `extraction/history_processors.py`: below the soft
token watermark they behave exactly like the conservative processors; once
cumulative usage crosses it they escalate — pre-write image trimming and tighter
text-compaction thresholds. The watermark is read from
`XBRL_SOFT_COMPACT_TOKENS` at call time.
"""

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)

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


def _response() -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="thinking")])


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


def _old_large_text_history():
    """An old verify_totals result (800 chars, 3 turns ago) + a fresher one."""
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
    return [old, _response(), _response(), _response(), fresh]


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
    out = compact_old_text_results_ctx(_Ctx(1000), _old_large_text_history())
    # 3 turns < 6 and 800 chars < 1500 → conservative thresholds skip it.
    assert not _is_compacted(out)


def test_over_watermark_compacts_with_aggressive_thresholds(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    out = compact_old_text_results_ctx(_Ctx(70000), _old_large_text_history())
    # 3 turns >= 2 and 800 chars >= 500 → aggressive thresholds compact it.
    assert _is_compacted(out)


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
