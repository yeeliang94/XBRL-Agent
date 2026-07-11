"""PydanticAI `history_processors` for token-cost reduction.

These are **pure functions over the model-message list** that run just before
each model call. They strip stale, re-billed payloads (old page images, the
repeated bulky template summary) out of the outbound request without touching
extraction logic.

**The processed history IS what gets persisted.** pydantic-ai 1.x writes each
turn's processed messages back onto the run state
(`ctx.state.message_history[:] = messages` in `_agent_graph.py`), so
`result.all_messages()` — and therefore every saved
`*_conversation_trace.json` — reflects the final compacted state, not the
original verbatim exchange. A placeholder in a trace ("Page N was viewed
earlier…") means the content was compacted on a LATER turn; the model saw it
in full while it was fresh. `agent_tracing._write_trace` stamps each trace
file with a `trace_note` saying exactly this, because a 2026-07-07 diagnosis
(run 63) misread those placeholders as "the agent wrote while blind".

Why this exists: agent runs re-send the entire conversation history on every
turn (no trimming). The dominant waste is old image blobs and the one-time
template summary being re-billed on every subsequent turn. See
`docs/Archive/PLAN-token-cost-reduction.md`.

Both processors are generic over tool name — scout's image tool is
`view_pages`, extraction/notes use `view_pdf_pages`, and `read_template`
is shared — so a single pair of processors covers all three subsystems.

Purity contract: the input message list is never mutated. PydanticAI message
parts are mutable dataclasses, so mutating them in place would also corrupt the
in-memory conversation and persisted traces, not just the outbound request.
Every changed part is rebuilt with `dataclasses.replace(...)` on copied lists.
"""

from __future__ import annotations

import dataclasses
import os
import re
from typing import List

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

# Marker the image tools emit before each page image, e.g. "=== Page 12 ===".
_PAGE_MARKER_RE = re.compile(r"===\s*Page\s+(\d+)\s*===", re.IGNORECASE)

# The tools that commit extracted data to disk. Once one of these has
# succeeded, the workbook — not the page images — is the source of truth, so
# older images can be trimmed. Before the first successful write the agent is
# still reading and cross-referencing multiple pages, so images are kept whole.
# write_facts is the face-extraction write tool (rewrite Phase 3 renamed it
# from fill_workbook, which is kept here for back-compat with message
# histories recorded before the rename). write_notes is the notes write tool.
_WRITE_TOOL_NAMES = frozenset({"write_facts", "fill_workbook", "write_notes"})

# Replacement text for collapsed duplicate template summaries — also the
# idempotency sentinel (an already-collapsed copy is never re-replaced).
_DUPLICATE_TEMPLATE_POINTER = "Template structure already provided above."

# A write counts as a trimming boundary only when it actually COMMITTED data —
# matched by a success string carrying a non-zero count. The count matters, not
# just the prefix: notes/agent.py emits "Wrote 0 row(s) … Writer errors: …" and
# "Collected 0 payload(s) … Rejected …" on the *failure* path too (the prefix is
# unconditional), and fill_workbook's failure path returns "Failed to fill
# workbook. Errors: …". Gating on count >= 1 keeps a failed/no-op write from
# flipping the agent into post-write trimming and stripping the source pages it
# still needs to retry — the run_id=126 failure mode the stage-aware rule exists
# to prevent. A partial write ("Wrote 3 row(s) … Writer errors: 1 skipped") is
# correctly a boundary: 3 rows really landed. Coupled to the tool return strings
# in extraction/agent.py (fill_workbook) and notes/agent.py (write_notes /
# _sub_agent_sink_write); pinned by test_history_processors.py.
_WRITE_SUCCESS_PATTERNS = (
    re.compile(r"^Successfully wrote\s+(\d+)\s+field", re.IGNORECASE),
    re.compile(r"^Wrote\s+(\d+)\s+row", re.IGNORECASE),
    re.compile(r"^Collected\s+(\d+)\s+payload", re.IGNORECASE),
)

# read_template returns a summary that opens with this banner on its first
# sheet block. Used to recognise a template-summary tool return generically,
# without hard-coding the tool name.
_TEMPLATE_SUMMARY_MARKER = "=== Sheet:"


def _tool_return_parts(messages: List[ModelMessage]):
    """Yield (message_index, part_index, part) for every ToolReturnPart."""
    for mi, msg in enumerate(messages):
        if not isinstance(msg, ModelRequest):
            continue
        for pi, part in enumerate(msg.parts):
            if isinstance(part, ToolReturnPart):
                yield mi, pi, part


def _part_has_image(part: ToolReturnPart) -> bool:
    """True if this tool return carries at least one image blob."""
    content = part.content
    if isinstance(content, list):
        return any(isinstance(item, BinaryContent) for item in content)
    return isinstance(content, BinaryContent)


def _nearest_page_number(content: List[object], image_index: int) -> str:
    """Find the page number from the nearest preceding `=== Page N ===` marker.

    The image tools always emit the text marker immediately before its image,
    so we scan backwards. Returns the digit string, or "?" if no marker found
    (keeps the placeholder useful even if the marker shape ever changes).
    """
    for j in range(image_index - 1, -1, -1):
        item = content[j]
        if isinstance(item, str):
            m = _PAGE_MARKER_RE.search(item)
            if m:
                return m.group(1)
    return "?"


def _replace_part(
    messages: List[ModelMessage],
    message_index: int,
    part_index: int,
    new_part: ToolReturnPart,
) -> List[ModelMessage]:
    """Return a new message list with one part swapped — no mutation in place."""
    out = list(messages)
    old_msg = out[message_index]
    new_parts = list(old_msg.parts)
    new_parts[part_index] = new_part
    out[message_index] = dataclasses.replace(old_msg, parts=new_parts)
    return out


def _part_text(part: ToolReturnPart) -> str:
    """The string portion of a tool return's content (joins list str items)."""
    content = part.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(item for item in content if isinstance(item, str))
    return ""


def _is_successful_write(part: ToolReturnPart) -> bool:
    """True only for a write tool return that actually committed >= 1 row/field.

    Gates on a success string with a non-zero count, not just the tool name — a
    failed or no-op write (malformed JSON, refused fill, "Wrote 0 row(s)",
    "Collected 0 payload(s)") must NOT count as a boundary, or its earlier
    source pages get stripped before the agent can retry.
    """
    if part.tool_name not in _WRITE_TOOL_NAMES:
        return False
    text = _part_text(part).lstrip()
    for pattern in _WRITE_SUCCESS_PATTERNS:
        m = pattern.match(text)
        if m and int(m.group(1)) >= 1:
            return True
    return False


def _last_write_message_index(messages: List[ModelMessage]) -> int | None:
    """Message index of the most recent successful write tool return, or None."""
    last: int | None = None
    for mi, _pi, part in _tool_return_parts(messages):
        if _is_successful_write(part):
            last = mi
    return last


def strip_stale_images(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Trim re-billed page images that the agent no longer needs to *see*.

    The rule is stage-aware, not a fixed window — this is the fix for the
    run_id=126 regression where a one-batch window made multi-page agents
    thrash (re-fetching the same pages 10+ times and never writing):

    - **Before the first successful write** (`fill_workbook` / `write_notes`),
      keep *every* image. This is the discovery/extraction phase where the
      agent legitimately needs several pages visible at once to cross-reference
      them; stripping here is what caused the loop. This holds
      **unconditionally — including under token pressure** (see the
      scanned-thrash note below).
    - **After a write**, the workbook is the source of truth, so strip images
      from batches that *precede* the most recent write. The most recent image
      batch is always kept so the agent is never fully blinded, and any images
      viewed since the last write (the current fix cycle) are kept too.

    **Why pre-write stripping is never done (scanned-thrash fix, 2026-06-20):**
    an earlier "Plan 2" escalation stripped pre-write images down to the newest
    batch once the agent crossed a soft token watermark. On *scanned* PDFs each
    page image is ~1–2 MB, so a normal extraction crosses the watermark within a
    handful of `view_pdf_pages` calls — long before it has read enough of the
    (hard-to-OCR) table to write anything. Stripping there made the agent forget
    the pages it had just viewed, re-fetch them, cross the watermark again, and
    loop forever without ever calling `write_facts` (observed: SOFP re-fetched
    page 10 nine times and produced no output). Carrying the images is strictly
    better than that thrash-lock; the tokens are reclaimed by post-write
    trimming the moment the agent commits a fact. Token pressure is still
    relieved pre-write — but by `compact_old_text_results` (bulky *text*
    payloads), never by blinding the agent to its own pages.

    Stripped `BinaryContent` blobs become a one-line placeholder that preserves
    the `=== Page N ===` markers and actively discourages re-fetching.

    Generic over tool name: matches any `ToolReturnPart` whose content carries
    `BinaryContent`, so it covers `view_pdf_pages` (extraction/notes) and
    `view_pages` (scout).
    """
    image_parts = [
        (mi, pi, part)
        for mi, pi, part in _tool_return_parts(messages)
        if _part_has_image(part)
    ]
    if len(image_parts) <= 1:
        # Nothing stale yet — the only (or zero) image batch is the current one.
        return messages

    last_write_idx = _last_write_message_index(messages)
    if last_write_idx is None:
        # Discovery/extraction phase — no data committed yet. Keep ALL images
        # so the agent can hold multiple pages in view to cross-reference them.
        # Unconditional, including under token pressure: stripping here is the
        # run_id=126 / scanned-PDF thrash-lock (see the function docstring).
        return messages

    # Protect the single most recent image batch regardless of where it sits.
    newest_image_idx = image_parts[-1][0]

    # Post-write: the workbook is the source of truth, so strip images from
    # batches that precede the most recent successful write.
    keep_from = last_write_idx

    out = messages
    for mi, pi, part in image_parts:
        # Keep: the newest batch, and anything viewed at/after the write
        # boundary (the current fix cycle). Strip only images that predate it.
        if mi >= keep_from or mi == newest_image_idx:
            continue
        content = part.content
        if not isinstance(content, list):
            continue
        new_content: List[object] = []
        for idx, item in enumerate(content):
            if isinstance(item, BinaryContent):
                page = _nearest_page_number(content, idx)
                new_content.append(
                    f"Page {page} was viewed earlier and its data is already "
                    f"captured in the workbook; do not re-open it just to "
                    f"refresh context."
                )
            else:
                new_content.append(item)
        new_part = dataclasses.replace(part, content=new_content)
        out = _replace_part(out, mi, pi, new_part)

    return out


# --- Stale text-result compaction (item 30) -------------------------------
#
# Long extractions re-bill every old tool result on every turn: a verbose
# `verify_totals` imbalance dump from turn 5 rides along, unchanged, to turn 35.
# `strip_stale_images` only trims image blobs; bulky *text* payloads had no
# equivalent. This processor replaces stale, oversized text tool-returns with a
# one-line pointer so the agent still sees that the call happened (and was
# superseded) without paying for the full body every turn.
#
# Defaults are deliberately conservative — only results that are BOTH old
# (older than COMPACT_AFTER_TURNS model responses ago) AND large
# (>= COMPACT_MIN_CHARS) are touched, and the most recent result of each tool
# is always kept verbatim so the agent's current working state is never
# summarised away.

# A result is eligible only once this many model responses have happened after
# it — i.e. it is at least this many turns in the past.
COMPACT_AFTER_TURNS = 6

# Only compact payloads at least this large; small results aren't worth the
# fidelity loss and a one-line summary wouldn't save meaningful tokens.
COMPACT_MIN_CHARS = 1500

# Plan 2 — aggressive thresholds applied once the agent crosses the soft token
# watermark: compact sooner (2 turns vs 6) and smaller (500 chars vs 1500) so a
# runaway agent stops re-billing old payloads every turn. Deterministic
# placeholder substitution (no LLM call), so this can run under token pressure
# without the "summariser degrades under pressure" risk.
COMPACT_AGGRESSIVE_AFTER_TURNS = 2
COMPACT_AGGRESSIVE_MIN_CHARS = 500

# Soft watermark default (cumulative tokens). Chosen high enough that only a
# genuinely runaway agent reaches it — normal runs never escalate. 0 / invalid
# disables escalation entirely (the processors behave exactly as before).
_DEFAULT_SOFT_COMPACT_TOKENS = 60000


def resolve_soft_compact_tokens() -> int:
    """``XBRL_SOFT_COMPACT_TOKENS``: cumulative-token watermark for escalation.

    Read at call time so tests/operators can toggle it. Unset → the default;
    explicit ``0`` or a non-numeric value → disabled (returns 0).
    """
    raw = os.environ.get("XBRL_SOFT_COMPACT_TOKENS")
    if raw is None:
        return _DEFAULT_SOFT_COMPACT_TOKENS
    try:
        v = int(raw)
    except ValueError:
        return 0
    return v if v > 0 else 0


def _cumulative_tokens(ctx) -> int:
    """Cumulative total tokens from a pydantic-ai RunContext, defensively.

    ``ctx.usage`` is a ``RunUsage`` whose ``total_tokens`` is the running sum
    across the agent's turns. Any shape surprise (missing usage, None) reads as
    0 so escalation simply never triggers rather than crashing the request.
    """
    usage = getattr(ctx, "usage", None)
    if usage is None:
        return 0
    try:
        return int(getattr(usage, "total_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _over_soft_watermark(ctx) -> bool:
    """True when cumulative usage has crossed the (enabled) soft watermark."""
    limit = resolve_soft_compact_tokens()
    return limit > 0 and _cumulative_tokens(ctx) >= limit


# Cache-economics additions (Harness-learnings Item 3, docs/PLAN-pydantic-ai-v2.md
# D.3): every history rewrite changes the outbound prompt bytes and therefore
# invalidates the provider's prompt cache from the earliest changed message.
# A rewrite that reclaims only a few hundred characters costs more (full-price
# re-read of the cached prefix) than it saves, so `compact_old_text_results`
# can skip the whole edit batch when the total reclaim is below this floor.
# Chars, not tokens — cheap, deterministic, and proportional (~4 chars/token).
# DEFAULT 0 = disabled (opt-in first, same philosophy as the token budget):
# the conservative-path compaction behavior is pinned by
# tests/test_history_compaction.py and must not change silently; enable via
# XBRL_COMPACT_MIN_RECLAIM_CHARS once Telemetry evidence supports a floor.
_DEFAULT_COMPACT_MIN_RECLAIM_CHARS = 0


def resolve_min_reclaim_chars() -> int:
    """``XBRL_COMPACT_MIN_RECLAIM_CHARS``: skip-rewrite floor (0 disables)."""
    raw = os.environ.get("XBRL_COMPACT_MIN_RECLAIM_CHARS")
    if raw is None:
        return _DEFAULT_COMPACT_MIN_RECLAIM_CHARS
    try:
        v = int(raw)
    except ValueError:
        return 0
    return v if v > 0 else 0


def estimate_outbound_chars(messages: List[ModelMessage]) -> int:
    """Rough size of the outbound history: total TEXT characters.

    Measures what we are about to re-send (unlike the cumulative
    ``ctx.usage`` watermark, which is monotonic for the whole run and never
    releases). TEXT ONLY — image/binary payloads contribute nothing, which
    is a documented blind spot: the image path has its own stage-aware
    processor and must never be driven by a size trigger (scanned-PDF
    thrash fix). ~4 chars/token if a token figure is needed.
    """
    total = 0
    for msg in messages:
        for part in getattr(msg, "parts", ()):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                total += sum(len(c) for c in content if isinstance(c, str))
    return total


def resolve_outbound_escalation_chars() -> int:
    """``XBRL_SOFT_COMPACT_OUTBOUND_CHARS``: outbound-size escalation trigger.

    DEFAULT 0 = disabled (opt-in first, like the token budget): the
    cumulative watermark is incident-tuned and stays the primary trigger;
    this adds a second, size-of-what-we-send trigger for operators to
    enable once Telemetry evidence supports a threshold.
    """
    raw = os.environ.get("XBRL_SOFT_COMPACT_OUTBOUND_CHARS", "")
    if not raw:
        return 0
    try:
        v = int(raw)
    except ValueError:
        return 0
    return v if v > 0 else 0


def _over_outbound_watermark(messages: List[ModelMessage]) -> bool:
    limit = resolve_outbound_escalation_chars()
    return limit > 0 and estimate_outbound_chars(messages) >= limit


def _model_responses_after(
    messages: List[ModelMessage], message_index: int
) -> int:
    """Count model responses after `message_index` — i.e. how many turns ago.

    A turn boundary is a `ModelResponse` (the model's reply that closes a
    request→response exchange). A `ToolReturnPart` lives in a `ModelRequest`,
    so the number of `ModelResponse`s that follow its message is how many turns
    have elapsed since that result was produced.
    """
    return sum(
        1
        for msg in messages[message_index + 1 :]
        if isinstance(msg, ModelResponse)
    )


def _summarize_text_result(tool_name: str, text: str, turns_ago: int) -> str:
    """One-line replacement for a compacted result, preserving a breadcrumb.

    Keeps the tool name, the age, the original size, and the first line of the
    payload so the agent can still tell what the call was and that a fresher
    result supersedes it — without re-billing the full body.
    """
    stripped = text.strip()
    first_line = stripped.splitlines()[0][:120] if stripped else ""
    return (
        f"[{tool_name} result from ~{turns_ago} turns ago, {len(text)} chars "
        f"— compacted to save tokens; a more recent result for this tool "
        f"appears later in the conversation. First line was: {first_line}]"
    )


def compact_old_text_results(
    messages: List[ModelMessage],
    *,
    after_turns: int = COMPACT_AFTER_TURNS,
    min_chars: int = COMPACT_MIN_CHARS,
    min_reclaim: "int | None" = None,
) -> List[ModelMessage]:
    """Replace stale, oversized text tool-results with a one-line summary.

    ``after_turns`` / ``min_chars`` default to the conservative module
    constants; the token-aware wrapper passes the aggressive thresholds once
    the agent crosses the soft watermark (Plan 2).

    Companion to `strip_stale_images` (which owns image blobs) — this targets
    bulky *text* payloads (verbose `verify_totals` dumps, long error lists).

    Rules (all must hold for a result to be compacted):

    - **Not the most recent result of its tool.** The latest result of every
      tool name is kept verbatim — that's the agent's current working state.
    - **Old enough.** At least `COMPACT_AFTER_TURNS` model responses have
      happened since the result was produced.
    - **Large enough.** Rendered text is at least `COMPACT_MIN_CHARS`.
    - **Not an image batch.** Image-carrying returns are `strip_stale_images`'s
      job; this processor never touches them.
    - **Not a `read_template` summary.** The template map is referenced
      repeatedly and is already deduped by `strip_duplicate_template`; never
      compact it here.
    - **Not a write confirmation.** A `write_facts` / `write_notes` /
      `fill_workbook` return is the agent's only record of WHAT it already
      wrote (rows, refusals, partial errors). Compacting an old one risks the
      agent forgetting committed rows and looping on re-writes — the exact
      class of regression the stage-aware image rule exists to prevent.
      Exempted the same way `read_template` summaries are (code-review fix,
      2026-06-13).

    Purity contract identical to the other processors — the input list is never
    mutated; every changed part is rebuilt with `dataclasses.replace`.
    """
    tool_returns = list(_tool_return_parts(messages))
    if not tool_returns:
        return messages

    # The most recent message index per tool name — exempt from compaction.
    # _tool_return_parts yields in message order, so the last write per tool
    # name wins.
    last_idx_per_tool: dict[str, int] = {}
    for mi, _pi, part in tool_returns:
        last_idx_per_tool[part.tool_name] = mi

    # Two-pass shape (cache-economics, Item 3): collect the edit batch first,
    # then apply it ONLY if the total reclaim clears the min-reclaim floor.
    # Rationale: any rewrite invalidates the provider prompt cache from the
    # earliest changed message — a tiny reclaim costs more than it saves.
    # Prior turns' placeholders persist in run state (gotcha #6), so the
    # batch only ever contains genuinely-new compactions.
    edits: list[tuple[int, int, ToolReturnPart, str]] = []
    reclaimed = 0
    for mi, pi, part in tool_returns:
        # Image batches and template summaries are owned by the other two
        # processors; never double-handle them here.
        if _part_has_image(part) or _is_template_summary(part):
            continue
        # Write confirmations are the durable record of what already landed
        # in the workbook/DB — never compact them (see docstring rule).
        if part.tool_name in _WRITE_TOOL_NAMES:
            continue
        # Keep the freshest result of each tool verbatim.
        if last_idx_per_tool.get(part.tool_name) == mi:
            continue
        turns_ago = _model_responses_after(messages, mi)
        if turns_ago < after_turns:
            continue
        text = _part_text(part)
        if len(text) < min_chars:
            continue
        summary = _summarize_text_result(part.tool_name, text, turns_ago)
        reclaimed += max(len(text) - len(summary), 0)
        edits.append((mi, pi, part, summary))

    if not edits:
        return messages
    # min_reclaim=None → env default; callers in AGGRESSIVE mode pass 0 so
    # the shed-everything escalation is never suppressed by cache economics
    # (pinned by test_history_processor_escalation — over the watermark the
    # run is already drowning; the cache is worth less than the reclaim).
    effective_min = resolve_min_reclaim_chars() if min_reclaim is None else min_reclaim
    if reclaimed < effective_min:
        return messages

    out = messages
    for mi, pi, part, summary in edits:
        out = _replace_part(out, mi, pi, dataclasses.replace(part, content=summary))
    return out


def _is_template_summary(part: ToolReturnPart) -> bool:
    """True if this tool return looks like a read_template structure summary."""
    content = part.content
    if isinstance(content, str):
        return _TEMPLATE_SUMMARY_MARKER in content
    if isinstance(content, list):
        return any(
            isinstance(item, str) and _TEMPLATE_SUMMARY_MARKER in item
            for item in content
        )
    return False


def strip_duplicate_template(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Collapse repeated read_template summaries to a one-line pointer.

    The ~12k-token template summary is otherwise re-billed on every turn once
    the agent has called `read_template` more than once. The first copy is kept
    intact; every later copy is replaced with a short pointer back to it. This
    is the per-turn token removal that works regardless of provider (caching is
    a separate, later concern).
    """
    summary_parts = [
        (mi, pi, part)
        for mi, pi, part in _tool_return_parts(messages)
        if _is_template_summary(part)
    ]
    if len(summary_parts) <= 1:
        return messages

    # Keep the first; replace the rest with a pointer. Copies already
    # collapsed on a previous turn (the processed history persists back onto
    # run state, gotcha #6) are skipped so a repeat pass is a true no-op —
    # same-value replaces still rebuild message objects for nothing.
    out = messages
    for mi, pi, _part in summary_parts[1:]:
        if _part.content == _DUPLICATE_TEMPLATE_POINTER:
            continue
        new_part = dataclasses.replace(
            _part, content=_DUPLICATE_TEMPLATE_POINTER
        )
        out = _replace_part(out, mi, pi, new_part)

    return out


# --- Token-aware wrappers (Plan 2) ----------------------------------------
#
# pydantic-ai inspects each history processor's signature: a `(ctx, messages)`
# processor receives the RunContext (and thus cumulative `ctx.usage`), while a
# bare `(messages)` one does not. These thin wrappers read the running token
# total and switch the two compacting processors into aggressive mode once the
# soft watermark is crossed. They are what `extraction/agent.py` registers;
# the pure cores above stay `(messages)`-callable so scout/notes and the unit
# tests keep using them unchanged.
#
# The first parameter MUST be annotated `RunContext` — pydantic-ai 1.77's
# `takes_run_context` detects the ctx variant purely from that type hint
# (`_utils.get_first_param_type`). A bare/un-annotated `ctx` is treated as a
# no-ctx `(messages)` processor and called with a single positional arg, raising
# `strip_stale_images_ctx() missing 1 required positional argument: 'messages'`.


def strip_stale_images_ctx(
    ctx: RunContext, messages: List[ModelMessage]
) -> List[ModelMessage]:
    """`strip_stale_images` registered with the ctx signature.

    Image trimming no longer escalates with token usage — pre-write images are
    kept unconditionally (scanned-thrash fix, 2026-06-20) and post-write
    trimming already runs at every turn — so `ctx` is intentionally unused
    here. The wrapper is retained (rather than registering the bare
    `strip_stale_images`) only so pydantic-ai's ctx-detection contract stays
    symmetric with `compact_old_text_results_ctx`, which DOES still read the
    watermark. The `RunContext` annotation on the first parameter is load-bearing
    for that detection (see the module note above and
    `test_history_processor_escalation.py::test_ctx_wrappers_detected_as_run_context_taking`).
    """
    return strip_stale_images(messages)


def compact_old_text_results_ctx(
    ctx: RunContext, messages: List[ModelMessage]
) -> List[ModelMessage]:
    """Token-aware `compact_old_text_results`: tighter thresholds over budget.

    Escalates on EITHER trigger: the incident-tuned cumulative watermark, or
    (opt-in, default off) the outbound-size estimate — see
    ``resolve_outbound_escalation_chars``. OR-composition means the addition
    can only escalate more aggressively, never suppress the original trigger.
    """
    if _over_soft_watermark(ctx) or _over_outbound_watermark(messages):
        return compact_old_text_results(
            messages,
            after_turns=COMPACT_AGGRESSIVE_AFTER_TURNS,
            min_chars=COMPACT_AGGRESSIVE_MIN_CHARS,
            min_reclaim=0,  # escalated mode sheds everything — never gated
        )
    return compact_old_text_results(messages)


# ---------------------------------------------------------------------------
# Oversized-part clamp (Harness-learnings Item 4) -----------------------------
#
# Everything above targets OLD content (age thresholds). A single FRESH
# runaway part — a degenerate model response, a giant tool-call argument —
# has no defence and can alone blow the context budget. This processor
# clamps any response-side part over the threshold to head + tail with an
# explicit marker.
#
# Scope rules (deliberately narrower than the harness original):
# - RESPONSE side only (TextPart content, ToolCallPart args inside
#   ModelResponse). Request-side parts — user prompts, tool RETURNS — are
#   exempt wholesale: returns belong to the age-based compaction above.
# - Write-tool call args (_WRITE_TOOL_NAMES) are NEVER clamped: notes write
#   payloads legitimately run large (raw HTML behind the 30k rendered cap),
#   and the agent's memory of what it sent must not be corrupted mid-run.
# - Clamped args stay VALID JSON ({"_clamped": "<head>…<tail>"}) so the
#   provider never sees malformed function arguments.
# - Only clamps when the result actually shrinks.
# - Threshold default 40_000 chars — comfortably above any legitimate notes
#   payload; only true runaways qualify. ``XBRL_CLAMP_MAX_PART_CHARS``
#   overrides; 0 disables the processor.
# ---------------------------------------------------------------------------

_DEFAULT_CLAMP_MAX_PART_CHARS = 40_000
_CLAMP_KEEP_HEAD = 2000
_CLAMP_KEEP_TAIL = 2000


def resolve_clamp_max_part_chars() -> int:
    """``XBRL_CLAMP_MAX_PART_CHARS``: per-part clamp threshold (0 disables)."""
    raw = os.environ.get("XBRL_CLAMP_MAX_PART_CHARS")
    if raw is None:
        return _DEFAULT_CLAMP_MAX_PART_CHARS
    try:
        v = int(raw)
    except ValueError:
        return 0
    return v if v > 0 else 0


def _clamp_text(text: str) -> str:
    removed = len(text) - _CLAMP_KEEP_HEAD - _CLAMP_KEEP_TAIL
    return (
        text[:_CLAMP_KEEP_HEAD]
        + f"\n[clamped: removed {removed} of {len(text)} characters]\n"
        + text[-_CLAMP_KEEP_TAIL:]
    )


def clamp_oversized_parts(messages: List[ModelMessage]) -> List[ModelMessage]:
    """Clamp fresh runaway response-side parts to head+tail with a marker.

    Pure over its inputs like every processor here: rebuilt lists +
    ``dataclasses.replace``, never in-place mutation.
    """
    limit = resolve_clamp_max_part_chars()
    if limit <= 0:
        return messages

    out: List[ModelMessage] = []
    changed_any = False
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            out.append(msg)
            continue
        new_parts = []
        changed = False
        for part in msg.parts:
            if isinstance(part, TextPart) and isinstance(part.content, str):
                text = part.content
                if len(text) > limit:
                    clamped = _clamp_text(text)
                    if len(clamped) < len(text):  # only if it actually shrinks
                        part = dataclasses.replace(part, content=clamped)
                        changed = True
            elif isinstance(part, ToolCallPart):
                if part.tool_name in _WRITE_TOOL_NAMES:
                    new_parts.append(part)
                    continue
                args = part.args
                text = args if isinstance(args, str) else None
                if text is None and isinstance(args, dict):
                    # Cheap size probe without a full serialize for small args.
                    text = str(args)
                if text is not None and len(text) > limit:
                    clamped = _clamp_text(text)
                    if len(clamped) < len(text):
                        # Valid JSON object — providers must never receive
                        # malformed function arguments.
                        part = dataclasses.replace(part, args={"_clamped": clamped})
                        changed = True
            new_parts.append(part)
        if changed:
            out.append(dataclasses.replace(msg, parts=new_parts))
            changed_any = True
        else:
            out.append(msg)
    return out if changed_any else messages
