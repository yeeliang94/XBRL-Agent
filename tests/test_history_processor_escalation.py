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


def test_over_watermark_strips_prewrite_to_newest(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "60000")
    msgs = _three_prewrite_batches()
    out = strip_stale_images_ctx(_Ctx(70000), msgs)
    # Aggressive: only the newest batch survives.
    assert _count_images(out) == 1


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
