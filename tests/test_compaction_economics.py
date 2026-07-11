"""Pinning tests for Harness-learnings Items 3+4 (docs/PLAN-pydantic-ai-v2.md D.3).

Item 3 — cache-aware compaction:
- min-reclaim gate: a rewrite batch that reclaims too little is skipped
  entirely (the provider prompt cache is worth more than a tiny saving);
- strip_duplicate_template idempotency: a second pass over already-
  collapsed copies is a TRUE no-op (returns the input list object);
- outbound-size estimator + opt-in escalation trigger (default OFF; the
  incident-tuned cumulative watermark stays primary — OR-composed so the
  addition can only escalate more, never less).

Item 4 — oversized-part clamp:
- fresh runaway response-side parts (TextPart content / ToolCallPart args)
  clamp to head+tail with an explicit marker;
- clamped args stay valid JSON-able dicts;
- write-tool args and request-side parts are NEVER clamped;
- disabled via XBRL_CLAMP_MAX_PART_CHARS=0; only clamps when it shrinks.
"""

from types import SimpleNamespace

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from extraction.history_processors import (
    _DUPLICATE_TEMPLATE_POINTER,
    clamp_oversized_parts,
    compact_old_text_results,
    compact_old_text_results_ctx,
    estimate_outbound_chars,
    strip_duplicate_template,
)


def _resp(text="ok"):
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_return(tool_name, content):
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id="c1")]
    )


def _aged(messages, turns=8):
    """Append `turns` model responses so earlier results are 'old enough'."""
    return messages + [_resp(f"turn {i}") for i in range(turns)]


# ---------------------------------------------------------------------------
# Item 3a — min-reclaim gate
# ---------------------------------------------------------------------------


def test_min_reclaim_gate_skips_small_batches(monkeypatch):
    # One stale result barely above the min_chars floor: reclaim would be
    # ~1600 chars < the 90_000 gate → whole batch skipped, identity return.
    monkeypatch.setenv("XBRL_COMPACT_MIN_RECLAIM_CHARS", "90000")
    msgs = _aged(
        [
            _tool_return("search_pdf_text", "x" * 1600),
            _tool_return("search_pdf_text", "fresh result kept"),
        ]
    )
    out = compact_old_text_results(msgs)
    assert out is msgs  # true no-op — cache-friendly identity


def test_min_reclaim_gate_allows_large_batches(monkeypatch):
    monkeypatch.setenv("XBRL_COMPACT_MIN_RECLAIM_CHARS", "2000")
    msgs = _aged(
        [
            _tool_return("search_pdf_text", "x" * 9000),
            _tool_return("search_pdf_text", "fresh result kept"),
        ]
    )
    out = compact_old_text_results(msgs)
    assert out is not msgs
    compacted = out[0].parts[0].content
    assert "x" * 9000 != compacted and len(compacted) < 9000


def test_min_reclaim_gate_disabled_by_default(monkeypatch):
    # DEFAULT is opt-in-off: without the env, behavior is byte-identical to
    # the pre-gate pipeline (pinned conservative-path semantics preserved).
    monkeypatch.delenv("XBRL_COMPACT_MIN_RECLAIM_CHARS", raising=False)
    msgs = _aged(
        [
            _tool_return("search_pdf_text", "x" * 1600),
            _tool_return("search_pdf_text", "fresh"),
        ]
    )
    out = compact_old_text_results(msgs)
    assert out is not msgs  # gate off by default → old behavior (rewrite)


# ---------------------------------------------------------------------------
# Item 3b — duplicate-template idempotency
# ---------------------------------------------------------------------------


def test_strip_duplicate_template_second_pass_is_identity():
    tmpl = "=== Sheet: SOFP ===\nrow 5: Assets\n" + "y" * 500
    msgs = [
        _tool_return("read_template", tmpl),
        _resp(),
        _tool_return("read_template", tmpl),
        _resp(),
        _tool_return("read_template", tmpl),
    ]
    once = strip_duplicate_template(msgs)
    assert once is not msgs
    # copies 2..N collapsed to the pointer, first kept verbatim
    assert once[0].parts[0].content == tmpl
    assert once[2].parts[0].content == _DUPLICATE_TEMPLATE_POINTER
    twice = strip_duplicate_template(once)
    assert twice is once  # true no-op on the second pass


# ---------------------------------------------------------------------------
# Item 3c — outbound estimator + opt-in escalation
# ---------------------------------------------------------------------------


def test_estimate_outbound_chars_counts_text_only():
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="abcd")]),
        _resp("efgh"),
    ]
    assert estimate_outbound_chars(msgs) == 8


def test_outbound_escalation_default_off(monkeypatch):
    # Huge outbound history, but the trigger is opt-in: without the env the
    # wrapper must NOT escalate (conservative thresholds still apply).
    monkeypatch.delenv("XBRL_SOFT_COMPACT_OUTBOUND_CHARS", raising=False)
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "0")  # cumulative off too
    ctx = SimpleNamespace(usage=SimpleNamespace(total_tokens=0))
    # A 3-turn-old result: young enough that only AGGRESSIVE thresholds
    # (after_turns=2) would touch it — conservative (6) leaves it alone.
    msgs = _aged([_tool_return("search_pdf_text", "x" * 3000),
                  _tool_return("search_pdf_text", "fresh")], turns=3)
    out = compact_old_text_results_ctx(ctx, msgs)
    assert out[0].parts[0].content == "x" * 3000


def test_outbound_escalation_opt_in(monkeypatch):
    monkeypatch.setenv("XBRL_SOFT_COMPACT_OUTBOUND_CHARS", "1000")
    monkeypatch.setenv("XBRL_SOFT_COMPACT_TOKENS", "0")
    monkeypatch.setenv("XBRL_COMPACT_MIN_RECLAIM_CHARS", "0")
    ctx = SimpleNamespace(usage=SimpleNamespace(total_tokens=0))
    msgs = _aged([_tool_return("search_pdf_text", "x" * 3000),
                  _tool_return("search_pdf_text", "fresh")], turns=3)
    out = compact_old_text_results_ctx(ctx, msgs)
    # Outbound trigger fired → aggressive thresholds → 3-turn-old compacted.
    assert out[0].parts[0].content != "x" * 3000


# ---------------------------------------------------------------------------
# Item 4 — oversized-part clamp
# ---------------------------------------------------------------------------


def test_clamps_runaway_text_part(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    big = "z" * 50_000
    msgs = [ModelResponse(parts=[TextPart(content=big)])]
    out = clamp_oversized_parts(msgs)
    text = out[0].parts[0].content
    assert "[clamped: removed" in text
    assert len(text) < len(big)
    assert text.startswith("z" * 100) and text.endswith("z" * 100)


def test_clamps_runaway_tool_args_to_valid_json_dict(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    msgs = [ModelResponse(parts=[
        ToolCallPart(tool_name="search_pdf_text", args="q" * 50_000, tool_call_id="c1"),
    ])]
    out = clamp_oversized_parts(msgs)
    args = out[0].parts[0].args
    assert isinstance(args, dict) and set(args) == {"_clamped"}
    assert "[clamped: removed" in args["_clamped"]


def test_never_clamps_write_tool_args(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    big = "h" * 50_000
    msgs = [ModelResponse(parts=[
        ToolCallPart(tool_name="write_notes", args=big, tool_call_id="c1"),
    ])]
    out = clamp_oversized_parts(msgs)
    assert out is msgs  # untouched — the write payload memory is protected


def test_never_touches_request_side(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    big = "r" * 50_000
    msgs = [
        ModelRequest(parts=[UserPromptPart(content=big)]),
        _tool_return("verify_totals", big),
    ]
    out = clamp_oversized_parts(msgs)
    assert out is msgs


def test_clamp_disabled_by_zero(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "0")
    msgs = [ModelResponse(parts=[TextPart(content="z" * 100_000)])]
    assert clamp_oversized_parts(msgs) is msgs


def test_small_parts_untouched(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    msgs = [ModelResponse(parts=[TextPart(content="short")])]
    assert clamp_oversized_parts(msgs) is msgs


def test_clamp_purity(monkeypatch):
    monkeypatch.setenv("XBRL_CLAMP_MAX_PART_CHARS", "10000")
    big = "z" * 50_000
    msgs = [ModelResponse(parts=[TextPart(content=big)])]
    clamp_oversized_parts(msgs)
    assert msgs[0].parts[0].content == big  # input never mutated


def test_registered_first_in_all_three_factories():
    import inspect
    import extraction.agent as face_mod
    import notes.agent as notes_mod
    import scout.agent as scout_mod

    for mod in (face_mod, notes_mod, scout_mod):
        src = inspect.getsource(mod)
        assert src.index("ProcessHistory(clamp_oversized_parts)") < src.index(
            "ProcessHistory(strip_stale_images"
        ), f"{mod.__name__}: clamp must run before the image processors"
