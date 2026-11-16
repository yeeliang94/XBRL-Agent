"""AI notes formatting pass for the Notes Review panel.

The model describes each table's layout, previews the drawn result against the
source PDF and corrects it. This module applies the final layout through
notes.format_patch, which verifies content preservation before any DB write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Collection
from typing import Any, Callable, Optional, Union

from bs4 import BeautifulSoup, Tag
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from agent_tracing import save_messages_trace
from db import repository as repo
from mtool.notes_decorate import NotesTableStyle
from mtool.notes_exporter import _resolve_note_html
from notes.format_patch import (
    FormatPatchError,
    apply_sheet_patch,
)
from notes.format_schema import NoteLayout, SheetLayout, UnresolvedNote, layout_to_dict
from notes.table_layout import LayoutError, layout_manifest, layout_operations
from notes.format_verify import table_rows as _direct_table_rows
from notes.render import fitting_font_size, render_note_preview
from notes.table_theme import firm_theme, resolve_run_theme
from model_settings import build_model_settings, describe_model_runtime

# One shared resolver (model_settings); the alias keeps each factory's call site.
from model_settings import configured_role_thinking_level as _thinking_level_for  # noqa: E402

from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes

logger = logging.getLogger(__name__)


def formatter_cell_is_candidate(cell: Any, style_sources: Optional[set[str | None]]) -> bool:
    """Use the same row eligibility for sheet selection and the formatter."""
    return bool((cell.html or "").strip()) and (
        style_sources is None or cell.style_source in style_sources
    )


list_formatter_cells_for_run = repo.list_formatter_cells_for_run


def filled_formatter_cells(conn, run_id: int, sheet: str) -> dict[int, Any]:
    """The sheet's filled canonical disclosure fields that hold text, by row."""
    return {c.row: c for c in list_formatter_cells_for_run(conn, run_id)
            if c.sheet == sheet and formatter_cell_is_candidate(c, None)
            and BeautifulSoup(c.html, "html.parser").get_text().strip()}

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "notes_formatter.md"

_STRUCTURED_OUTPUT_INSTRUCTION = """\
=== OUTPUT MODE: STRUCTURED ===
Return one SheetLayout through the enforced structured-output schema.
Do not write JSON text, Markdown, commentary, or a second copy of the layout.
"""

_JSON_FALLBACK_INSTRUCTION = """\
=== OUTPUT MODE: JSON FALLBACK ===
Return exactly one JSON object matching the SheetLayout fields described
above. Do not wrap it in Markdown and do not add commentary before or after it.
"""


# Cumulative per-click model-request budget across the formatter's agent.run
# passes — the initial pass with its layout previews, the output-rejection
# retry and the validation repair.
# Like the extraction MAX_AGENT_ITERATIONS cap, it
# MUST stay strictly below pydantic-ai's silent UsageLimits.request_limit=50
# (gotcha #18) — otherwise pydantic-ai fires its own UsageLimitExceeded from
# inside request preparation and we lose the structured "turn budget" message.
# Operators can tune via XBRL_NOTES_FORMATTER_MAX_REQUESTS; the value is
# clamped to _MAX_REQUESTS_CEILING so the sub-50 guarantee always holds.
_MAX_REQUESTS_CEILING = 45


def _resolve_max_requests() -> int:
    raw = os.environ.get("XBRL_NOTES_FORMATTER_MAX_REQUESTS", "")
    if not raw:
        return 16
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "XBRL_NOTES_FORMATTER_MAX_REQUESTS=%r is not an int; using 16", raw,
        )
        return 16
    if v <= 0:
        return 16
    if v > _MAX_REQUESTS_CEILING:
        logger.warning(
            "XBRL_NOTES_FORMATTER_MAX_REQUESTS=%d exceeds safe ceiling %d "
            "(pydantic-ai silent request_limit=50); clamping to %d.",
            v, _MAX_REQUESTS_CEILING, _MAX_REQUESTS_CEILING,
        )
        return _MAX_REQUESTS_CEILING
    return v


MAX_FORMATTER_REQUESTS = _resolve_max_requests()


def structured_output_enabled() -> bool:
    """Whether the formatter declares its output shape to the provider.

    On by default. `XBRL_NOTES_FORMATTER_STRUCTURED=0` returns the agent to
    free-form text plus `_parse_json_patch` repair.

    The switch exists because this could not be verified against a live model
    before shipping: declaring a nested schema removes the parse-failure class,
    but some models answer a complex schema less well than they answer prose.
    If formatter quality drops, this is a config flip rather than a redeploy.
    Read at call time so a test can toggle it.
    """
    return os.environ.get(
        "XBRL_NOTES_FORMATTER_STRUCTURED", ""
    ).strip().lower() not in ("0", "false", "no")


def _output_json(output: Any) -> str:
    """Normalise an agent result to the JSON text the screening path expects.

    With `output_type` set, `result.output` is a validated `SheetLayout`
    and `str()` of it would be a Python repr. Everything downstream — the
    screening gates, and the retry prompts that quote the model's own answer
    back to it — works on JSON text, so convert once here rather than
    branching in four places.
    """
    if isinstance(output, SheetLayout):
        return json.dumps(layout_to_dict(output), ensure_ascii=False)
    return str(output)

# Failure taxonomy persisted to notes_format_tasks.error_type (schema v27) —
# branch on these codes, not on the human-facing error prose. No CHECK
# constraint on the column (same rationale as runs.status).
FORMATTER_ERROR_TYPES = (
    "visual_review_failed", # the formatter reported notes it could not match to the source
    "timeout",              # wall-clock cap (XBRL_NOTES_FORMATTER_WALLCLOCK_S)
    "turn_budget",          # cumulative request cap (UsageLimitExceeded)
    "low_confidence",       # historical task rows only
    "validation_failed",    # bad JSON / content-preservation gate refused it
    "wrong_sheet",          # patch targeted a different sheet
    "no_unfinished_rows",   # filled sheet, but no PDF styling candidates
    "precondition_failed",  # no PDF / no filled cells / missing source pages
    "model_error",          # unexpected exception in the pass
    "restarted",            # server restarted while the pass was running
    "reverted",             # user reverted the pass's formatting
    "cancelled",            # Stop All during the automatic PDF pass
    "reviewer_running",     # auto pass skipped behind the notes reviewer
)


@dataclass
class NotesFormatterDeps:
    run_id: int
    db_path: str
    pdf_path: str
    sheet: str
    model: Union[str, Model]
    viewed_pages: set[int] = field(default_factory=set)
    pdf_page_count: int = 0
    # notes.formatter_activity.FormatterActivity for this pass, when reporting.
    activity: Any = None
    # What preview_layout draws: the launch HTML per row and the output style.
    rows: dict[int, str] = field(default_factory=dict)
    style: NotesTableStyle = field(default_factory=NotesTableStyle)
    previews: dict[int, int] = field(default_factory=dict)


def _report_tool(deps: NotesFormatterDeps, tool_name: str, args: dict):
    """Start reporting one tool call; returns the finisher for its result."""
    activity = deps.activity
    if activity is None:
        return lambda content: content
    call_id = activity.tool_started(tool_name, args)

    def finish(content):
        activity.tool_finished(call_id, tool_name, content)
        return content
    return finish


def create_notes_formatter_agent(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
) -> tuple[Agent, NotesFormatterDeps]:
    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    output_type = str
    if structured_output_enabled():
        output_type = SheetLayout
        base_prompt += "\n\n" + _STRUCTURED_OUTPUT_INSTRUCTION.strip()
    else:
        base_prompt += "\n\n" + _JSON_FALLBACK_INSTRUCTION.strip()
    agent, deps = _create_notes_style_agent(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
        model=model, prompt=base_prompt, output_type=output_type,
        cache_key="xbrl-notes-formatter",
    )

    @agent.tool
    def preview_layout(
        ctx: RunContext[NotesFormatterDeps], notes: list[NoteLayout],
    ) -> list[Union[str, BinaryContent]]:
        """Draw a draft layout and return pictures of the formatted notes.

        Nothing is saved. Each picture shows the note as review, Copy and
        mTool will receive it. A layout that does not fit its table is not
        drawn; the reason is returned so you can correct it. Compare each
        picture with the source page, then preview again only the rows you
        changed. Your final answer must still contain every layout.
        """
        finish = _report_tool(ctx.deps, "preview_layout", {"rows": [note.row for note in notes]})
        return finish(_preview_layout(ctx.deps, notes))

    return agent, deps


# Pictures returned by one preview call; more rows need a further call.
MAX_PREVIEW_PAGES = 12
# A first draft and two corrections per note; previews share the request cap.
MAX_PREVIEWS_PER_ROW = 3


def _preview_layout(
    deps: NotesFormatterDeps, notes: list[NoteLayout],
) -> list[Union[str, BinaryContent]]:
    layout = {"notes": [note.model_dump(exclude_none=True) for note in notes]}
    try:
        patch, _rejected, errors = _partition_layout(deps.rows, layout, deps.style.cell_padding_px)
    except FormatPatchError as exc:
        return [f"NOT DRAWN: {exc}"]
    results: list[Union[str, BinaryContent]] = [
        f"NOT DRAWN, correct this layout and preview it again: {error}" for error in errors.values()
    ]
    drawn = apply_sheet_patch(deps.rows, patch).rows
    pages_sent = 0
    for row in sorted(cell["row"] for cell in patch["cells"]):
        if deps.previews.get(row, 0) >= MAX_PREVIEWS_PER_ROW:
            results.append(f"Row {row} has no previews left. Return its final layout, or "
                           "report it in `unresolved` if it still differs from the source.")
            continue
        try:
            pages, too_wide_mm = render_note_preview(drawn[row], deps.style)
        except Exception as exc:  # noqa: BLE001 — a failed drawing must not end the pass
            logger.warning("Could not draw a preview run=%s row=%s", deps.run_id, row, exc_info=True)
            results.append(f"Row {row} could not be drawn: {exc}")
            continue
        if pages_sent + len(pages) > MAX_PREVIEW_PAGES and pages_sent:
            results.append(f"Row {row} was not drawn in this call; preview it in a separate call.")
            continue
        if len(pages) > MAX_PREVIEW_PAGES:
            results.append(f"Row {row}: only its first {MAX_PREVIEW_PAGES} pictures are shown.")
            pages = pages[:MAX_PREVIEW_PAGES]
        pages_sent += len(pages)
        deps.previews[row] = deps.previews.get(row, 0) + 1
        if too_wide_mm:
            fits = fitting_font_size(drawn[row], deps.style)
            advice = (f"It fits at {fits} pt: set font_size_pt {fits} on the wide table."
                      if fits else "It does not fit even at 8 pt: keep 8 pt and leave a house_style note.")
            results.append(f"Row {row}: a table is {too_wide_mm} mm wider than mTool's 170 mm text "
                           "width even at its narrowest. The picture is widened to show it whole, so "
                           f"this note's text wraps wider than in mTool. {advice}")
        for index, png in enumerate(pages, 1):
            results.append(f"=== Row {row} as formatted (picture {index} of {len(pages)}) ===")
            results.append(BinaryContent(data=png, media_type="image/png"))
    return results or ["Nothing to draw: the preview held no layouts."]


def _create_notes_style_agent(
    *, run_id: int, db_path: str, pdf_path: str, sheet: str,
    model: Union[str, Model], prompt: str, output_type: Any, cache_key: str,
) -> tuple[Agent, NotesFormatterDeps]:
    """Share source-viewing tools without selecting an agent's role."""
    deps = NotesFormatterDeps(
        run_id=run_id, db_path=str(db_path), pdf_path=pdf_path,
        sheet=sheet, model=model,
    )
    agent = Agent(
        model, deps_type=NotesFormatterDeps, system_prompt=prompt,
        output_type=output_type,
        model_settings=build_model_settings(
            model, cache_key=cache_key,
            thinking_level=_thinking_level_for("notes_formatter"),
        ),
        end_strategy="early",
    )

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesFormatterDeps], pages: list[int],
    ) -> list[Union[str, BinaryContent]]:
        """View specific source PDF pages as images."""
        finish = _report_tool(ctx.deps, "view_pdf_pages", {"pages": pages})
        return finish(_view_pdf_pages(ctx, pages))

    def _view_pdf_pages(
        ctx: RunContext[NotesFormatterDeps], pages: list[int],
    ) -> list[Union[str, BinaryContent]]:
        ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total = ctx.deps.pdf_page_count
        requested = [p for p in pages if isinstance(p, int)]
        invalid = sorted({p for p in requested if p < 1 or p > total})
        render_pages = sorted({p for p in requested if p not in invalid})
        results: list[Union[str, BinaryContent]] = []
        if invalid:
            results.append(f"Skipped invalid page(s) {invalid}. Valid range 1-{total}.")
        if not render_pages:
            results.append("No pages were rendered from this request.")
            return results
        rendered: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=min(len(render_pages), 8)) as pool:
            futures = {
                pool.submit(render_pages_to_png_bytes, ctx.deps.pdf_path, p, p): p
                for p in render_pages
            }
            for future in futures:
                page = futures[future]
                images = future.result()
                if images:
                    rendered[page] = images[0]
        for p in sorted(rendered):
            results.append(f"=== Page {p} ===")
            results.append(BinaryContent(data=rendered[p], media_type="image/png"))
        ctx.deps.viewed_pages.update(rendered.keys())
        return results

    @agent.tool
    async def zoom_pdf_region(
        ctx: RunContext[NotesFormatterDeps], page: int, region: str,
    ) -> list[Union[str, BinaryContent]]:
        """Re-render ONE region of a page so fine detail is legible.

        Use this only when a specific border, rule or column alignment you
        must decide cannot be read on the attached page. A whole page is
        downscaled, so hairline rules, double rules and alignment can be
        illegible — and a guess there is worse than leaving the cell alone.
        Zoom the one region holding that detail; do not zoom routinely or
        repeat a region.

        `region` is one of: top-half, bottom-half, left-half, right-half,
        top-third, middle-third, bottom-third, top-left, top-right,
        bottom-left, bottom-right, center — or 'full' for the whole page.
        Thirds overlap slightly, so a table crossing a boundary is intact in
        at least one of them.
        """
        finish = _report_tool(ctx.deps, "zoom_pdf_region", {"page": page, "region": region})
        return finish(await _zoom_pdf_region(ctx, page, region))

    async def _zoom_pdf_region(
        ctx: RunContext[NotesFormatterDeps], page: int, region: str,
    ) -> list[Union[str, BinaryContent]]:
        # Same tool the notes extraction agent has. The formatter is the one
        # role whose entire job is judging border extent and alignment, and
        # it was the only one without it (peer review, 2026-08-01).
        from notes.agent import (
            _NOTES_RENDER_DPI,
            _NOTES_RENDER_POLICY,
            _render_one_page_single_flight,
            resolve_zoom_region,
        )

        if ctx.deps.pdf_page_count == 0:
            ctx.deps.pdf_page_count = count_pdf_pages(ctx.deps.pdf_path)
        total = ctx.deps.pdf_page_count
        if not isinstance(page, int) or page < 1 or page > total:
            return [f"Invalid page {page!r}. Valid range is 1-{total}."]
        try:
            clip = resolve_zoom_region(region)
        except ValueError as exc:
            return [str(exc)]
        png = await _render_one_page_single_flight(
            ctx.deps.pdf_path, page, _NOTES_RENDER_DPI,
            policy=_NOTES_RENDER_POLICY, clip=clip,
        )
        # A zoom is a real read of the page — the write guard checks
        # viewed_pages, and a formatter that zoomed but never full-paged
        # would otherwise be refused its own grounded edit.
        ctx.deps.viewed_pages.add(page)
        label = "full page" if clip is None else region.strip().lower()
        return [
            f"=== Page {page} ({label}) ===",
            BinaryContent(data=png, media_type="image/png"),
        ]

    # `read_note_cell` was removed 2026-08-03 (peer review): it returned
    # sheet/row/label/html/evidence/source_pages for ONE row, and the user
    # prompt's CURRENT CELLS payload already carries exactly those fields for
    # EVERY row, plus `allowed_targets`, which the tool did not have. So a call
    # could only ever return a strict subset of what the model had already
    # read, at the cost of a turn — and its schema rode on every request.
    # If a targeted re-read is ever needed, add it back returning MORE than
    # the payload, not less.

    return agent, deps


@dataclass(frozen=True)
class _ScreenedPatch:
    patch: dict[str, Any]
    summary: str


def _screen_patch(
    output_text: str, sheet: str, *, revised: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[_ScreenedPatch], str]:
    """Run the gates every model output must pass before it may be applied:
    JSON parse and sheet match.

    Returns ``(error_return, screened, stage)`` where exactly one of
    ``error_return`` / ``screened`` is set and ``stage`` names the failing
    gate (``"parse" | "sheet" | "ok"``) so
    callers can special-case a parse failure (the repair pass keeps the
    original error).
    """
    prefix = "revised " if revised else ""
    try:
        patch = _parse_json_patch(output_text)
    except FormatPatchError as exc:
        return (
            {"ok": False, "error": str(exc), "error_type": "validation_failed"},
            None, "parse",
        )
    summary = str(patch.get("format_summary") or "").strip()
    if patch.get("sheet") != sheet:
        return (
            {
                "ok": False,
                "error": f"{prefix}formatter patch targeted the wrong sheet",
                "error_type": "wrong_sheet",
            },
            None, "sheet",
        )
    return None, _ScreenedPatch(patch, summary), "ok"


def _usage_fields(usage: RunUsage) -> dict[str, int]:
    """Flatten the accumulated cross-pass usage into the task-row columns."""
    return {
        "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_write_tokens", 0) or 0),
    }


def plain_failure(outcome: Optional[dict]) -> str:
    """One plain sentence for a failed pass, shown before any technical detail."""
    outcome = outcome or {}
    error_type = outcome.get("error_type")
    if error_type == "visual_review_failed":
        failed = {int(row) for row in outcome.get("failed_rows") or outcome.get("row_errors") or {}}
        count = len(failed) or 1
        notes = "1 note needs" if count == 1 else f"{count} notes need"
        recopied = " after re-copying its tables" if "table_recopy" in outcome else ""
        return (f"{notes} checking against the source PDF{recopied}. "
                f"Check {'it' if count == 1 else 'them'} in the Notes tab.")
    return {
        "timeout": "Formatting reached its time limit. Retry it from the Notes tab.",
        "turn_budget": "Formatting reached its request limit. Retry it from the Notes tab.",
        "validation_failed": "The formatting result could not be applied safely. Retry it from the Notes tab.",
        "wrong_sheet": "The formatting result was for a different sheet. Retry it from the Notes tab.",
        "model_error": "The AI request failed. Retry it from the Notes tab.",
        "precondition_failed": "Formatting could not start for this sheet.",
    }.get(error_type, "Formatting did not finish. Retry it from the Notes tab.")


async def run_notes_formatter(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
    output_dir: str = "",
    style_sources: Optional[set[str | None]] = None,
    rows: Optional[Collection[int]] = None,
    pass_started_at: Optional[str] = None,
    trace_label: Optional[str] = None,
    on_phase: Callable[[str], None] | None = None,
    on_event: Callable[[str, dict], None] | None = None,
    deadline: Optional[float] = None,
    activity: Any = None,
) -> dict[str, Any]:
    """Run the formatter pass and attach cross-pass token telemetry.

    ``rows`` limits the pass to those cells, so a caller can format disjoint
    row groups of one sheet concurrently; parts of one pass share
    ``pass_started_at`` for the revert snapshot and write distinct traces
    under ``trace_label``.

    Formatter usage accumulates across its model passes. Completed-response
    usage is saved to the shared ledger even when a later request fails or is
    cancelled.

    Each pass reports its own activity (``notes/formatter_activity.py``):
    live through ``on_event`` and saved on its ``run_agents`` row, keyed by
    ``sub_agent_id`` = the row's variant.

    ``deadline`` is the ``time.monotonic()`` at which the caller's
    ``asyncio.wait_for`` expires. A cancellation at or after it is the time
    limit, recorded as a failure; an earlier one is a deliberate stop.
    """
    from db import repository as repo
    from notes.formatter_activity import FormatterActivity
    from usage_metrics import split_usage
    from pricing import estimate_cost

    usage = RunUsage()
    model_name = model if isinstance(model, str) else getattr(model, "model_name", "")
    sub_agent_id = trace_label or sheet
    if activity is None:
        with repo.db_session(db_path) as conn:
            agent_id = repo.create_run_agent(conn, run_id, "NOTES_FORMATTING", variant=sub_agent_id, model=model_name)
        activity = FormatterActivity(sub_agent_id=sub_agent_id, sheet=sheet, on_event=on_event,
                                     db_path=db_path, run_agent_id=agent_id)
    activity.status("started", "Formatting started")

    def _phase(message: str) -> None:
        activity.status("working", message)
        if on_phase is not None:
            on_phase(message)

    outcome = None
    status = "failed"
    # The first pass and a post-re-copy pass write snapshots separately; one
    # shared start time keeps the second from wiping the first's revert copy.
    if pass_started_at is None:
        pass_started_at = datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z")
    try:
        outcome = await _run_notes_formatter_impl(
            run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
            model=model, output_dir=output_dir, usage=usage,
            style_sources=style_sources, rows=rows,
            pass_started_at=pass_started_at, trace_label=trace_label, on_phase=_phase,
            activity=activity,
        )
        structural = outcome.pop("miscopied", None)
        if structural:
            outcome = await _recopy_and_reformat(
                outcome, structural, run_id=run_id, db_path=db_path, pdf_path=pdf_path,
                sheet=sheet, model=model, model_name=model_name, output_dir=output_dir,
                usage=usage, pass_started_at=pass_started_at, trace_label=trace_label,
                on_phase=_phase, activity=activity,
            )
        outcome.update(_usage_fields(usage))
        status = "completed" if outcome.get("ok") else "failed"
        return outcome
    except asyncio.CancelledError:
        if deadline is not None and time.monotonic() >= deadline:
            outcome = {"error_type": "timeout", "error": "Formatting reached its time limit."}
        else:
            status = "cancelled"
        raise
    except Exception as exc:
        from pydantic_ai.exceptions import UsageLimitExceeded
        outcome = {"error_type": "turn_budget" if isinstance(exc, UsageLimitExceeded) else "model_error",
                   "error": f"{type(exc).__name__}: {exc}"}
        raise
    finally:
        # One aggregate ledger entry covers every formatter request. Task totals
        # are diagnostic, not additional ledger entries.
        try:
            metrics = split_usage(usage)
            usage_fields = dict(
                total_tokens=metrics.total_tokens, prompt_tokens=metrics.prompt_tokens,
                completion_tokens=metrics.completion_tokens,
                reasoning_tokens=metrics.thinking_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                turn_count=usage.requests, tool_call_count=usage.tool_calls,
                total_cost=estimate_cost(metrics.prompt_tokens, metrics.completion_tokens,
                                         metrics.thinking_tokens, model or model_name),
            )
        except Exception:  # noqa: BLE001 — the part still closes without usage
            logger.warning("Could not measure formatter usage run=%s sheet=%s", run_id, sheet, exc_info=True)
            usage_fields = {}
        activity.finish(status, outcome, **usage_fields)



async def _recopy_and_reformat(outcome: dict, structural: dict, *, run_id: int, db_path: str,
                               pdf_path: str, sheet: str, model, model_name: str,
                               output_dir: str, usage: RunUsage, pass_started_at,
                               trace_label, on_phase, activity=None) -> dict:
    """Re-copy wrongly copied tables from the PDF, then format the rebuilt notes.

    The rebuilt notes get one ordinary formatting pass. A table that cannot be
    re-copied keeps its original finding.
    """
    from notes.table_recopy import recopy_tables

    recopy = await recopy_tables(run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
                                 findings=structural, model=model, model_name=model_name,
                                 on_phase=on_phase)
    usage.incr(RunUsage(input_tokens=recopy["usage"]["prompt_tokens"],
                        output_tokens=recopy["usage"]["completion_tokens"]))
    result = {**outcome, "table_recopy": recopy}
    rebuilt = set(recopy["rebuilt_rows"])
    if not rebuilt:
        for row, reason in recopy["errors"].items():
            if str(row) in (result.get("row_errors") or {}) or row in (result.get("row_errors") or {}):
                key = row if row in result["row_errors"] else str(row)
                result["row_errors"][key] += "; " + reason
        return result
    try:
        final = await _run_notes_formatter_impl(
            run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet, model=model,
            output_dir=output_dir, usage=usage, style_sources=None, rows=sorted(rebuilt),
            pass_started_at=pass_started_at,
            trace_label=f"{trace_label or 'notes_format_' + sheet}_after_recopy", on_phase=on_phase,
            activity=activity,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — the first pass and re-copy are already saved
        # Keep their changed-row counts; only the rebuilt rows stay unresolved.
        logger.warning("Formatting re-copied notes failed run=%s sheet=%s", run_id, sheet,
                       exc_info=True)
        final = {"ok": False, "changed_rows": 0,
                 "error": f"Formatting the re-copied note failed: {type(exc).__name__}: {exc}"}
    # A re-copied table is not re-copied again; its finding stays for a person.
    final.pop("miscopied", None)
    errors = {int(row): error for row, error in (outcome.get("row_errors") or {}).items()
              if int(row) not in rebuilt}
    errors.update({int(row): error for row, error in (final.get("row_errors") or {}).items()})
    if not final.get("ok") and not final.get("row_errors"):
        errors.update({row: final.get("error") or "Re-copied note formatting is unresolved." for row in rebuilt})
    for row, reason in recopy["errors"].items():
        if row in errors:
            errors[row] += "; " + reason
    result.update(
        ok=not errors,
        changed_rows=int(outcome.get("changed_rows") or 0) + int(final.get("changed_rows") or 0),
        after_recopy=final,
        summary=(f"Re-copied {len(recopy['corrected'])} table(s) from the source PDF. "
                 + (final.get("summary") or "")),
    )
    warnings = recopy.get("warnings") or {}
    if warnings:
        # The note is right for this run, so not a failure, but the user must
        # hear that a rerun of the upload may bring the old table back.
        result["recopy_warnings"] = warnings
        result["summary"] += " " + " ".join(
            f"Row {row}: {text}" for row, text in sorted(warnings.items()))
    notes = {int(row): note for row, note in (outcome.get("layout_notes") or {}).items() if int(row) not in rebuilt}
    notes.update({int(row): note for row, note in (final.get("layout_notes") or {}).items()})
    result.pop("layout_notes", None)
    if notes:
        result["layout_notes"] = notes
    for key in ("error", "error_type", "failed_rows", "row_errors"):
        result.pop(key, None)
    if errors:
        result.update(error="; ".join(errors.values()), error_type="visual_review_failed",
                      failed_rows=sorted(errors), row_errors=errors)
    return result


async def _run_notes_formatter_impl(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
    output_dir: str,
    usage: RunUsage,
    style_sources: Optional[set[str | None]] = None,
    rows: Optional[Collection[int]] = None,
    pass_started_at: Optional[str] = None,
    trace_label: Optional[str] = None,
    on_phase: Callable[[str], None] | None = None,
    activity: Any = None,
) -> dict[str, Any]:
    if not pdf_path or not Path(pdf_path).exists():
        return {
            "ok": False, "error": "source PDF is not available",
            "error_type": "precondition_failed",
        }

    with repo.db_session(db_path) as conn:
        filled_cells = [
            c for c in list_formatter_cells_for_run(conn, run_id)
            if c.sheet == sheet and formatter_cell_is_candidate(c, None)
            and (rows is None or c.row in rows)
        ]
    cells = [
        c for c in filled_cells
        if formatter_cell_is_candidate(c, style_sources)
    ]
    # Never overwrite a note a person edited, even on an explicit retry.
    person_edited = sorted(c.row for c in cells if c.style_source == "human")
    cells = [c for c in cells if c.style_source != "human"]
    if not cells and person_edited:
        return {
            "ok": False,
            "error": "These notes were edited by a person; the formatter does not overwrite them.",
            "error_type": "precondition_failed", "person_edited_rows": person_edited,
        }
    if not cells:
        if filled_cells and style_sources is not None:
            return {
                "ok": False,
                "error": (
                    f"No unfinished PDF notes remain on {sheet}; filled notes "
                    "are already formatted or source-styled."
                ),
                "error_type": "no_unfinished_rows",
            }
        return {
            "ok": False, "error": f"no filled prose cells found on {sheet}",
            "error_type": "precondition_failed",
        }

    missing_page_errors = {c.row: "Source pages are missing; appearance remains unverified."
                           for c in cells if not c.source_pages}
    cells = [c for c in cells if c.source_pages]
    if not cells:
        return {
            "ok": False, "changed_rows": 0,
            "error": "Source pages are missing for rows " + ", ".join(map(str, missing_page_errors)),
            "error_type": "precondition_failed",
            "failed_rows": sorted(missing_page_errors), "row_errors": missing_page_errors,
        }

    rows_for_patch = {c.row: c.html for c in cells}
    page_set = sorted({p for c in cells for p in c.source_pages})
    # Handoff item 2: hand the agent the deterministic mTool size verdicts
    # (lite / flat / oversize) for this sheet, computed with the run's
    # resolved theme, so its judgement targets the right remedy per flag.
    output_theme = _resolve_notes_table_theme(db_path, run_id)
    size_signals = collect_size_signals(cells, output_theme)
    agent, deps = create_notes_formatter_agent(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet, model=model,
    )
    padding = NotesTableStyle.from_theme(output_theme).cell_padding_px
    if deps is not None:
        deps.activity = activity
        deps.rows = rows_for_patch
        deps.style = NotesTableStyle.from_theme(output_theme)
    # The old prompt forced a tool-only first model turn before the model could
    # do any work. That re-billed the full CURRENT CELLS payload (and every
    # prior part) on the second request. Attach the known source pages to the
    # initial request to avoid that extra tool-only turn, while keeping the
    # view/zoom tools available for targeted follow-up inspection.
    if activity is not None:
        activity.status("working", f"Loading {len(page_set)} source pages for formatting…")
    source_content, preloaded_pages = await _preload_source_pages(
        pdf_path, page_set,
    )
    pages_attached = len(preloaded_pages) == len(page_set)
    prompt = _build_user_prompt(
        sheet, cells, page_set, size_signals,
        source_pages_attached=pages_attached,
        appearance=output_theme,
    )
    initial_prompt: Any = [prompt, *source_content] if pages_attached else prompt
    if deps is not None:
        deps.viewed_pages.update(preloaded_pages)
    # One shared usage accumulator + request cap across every pass below, so
    # all formatting (initial with previews + output retry + validation repair)
    # is bounded — not each pass independently. UsageLimitExceeded surfaces as a structured "turn
    # budget" outcome in the API worker (mirrors the wall-clock timeout).
    limits = UsageLimits(request_limit=MAX_FORMATTER_REQUESTS)
    trace_messages: list = []

    async def _agent_run(user_prompt: Any):
        if activity is not None:
            activity.status("working", "Requesting formatting decisions…")
        result = await agent.run(
            user_prompt, deps=deps, usage=usage, usage_limits=limits,
            message_history=trace_messages,
        )
        # Re-write the trace after EVERY completed pass (best-effort, gotcha
        # #6): the trace is most valuable when a LATER pass times out or
        # errors — the completed passes are already on disk by then.
        # Follow-up passes need the source images/zooms, not just shared deps.
        # all_messages includes prior passes; replace rather than append them.
        trace_messages[:] = result.all_messages()
        if output_dir and trace_messages:
            save_messages_trace(
                trace_messages, output_dir, trace_label or f"notes_format_{sheet}",
                runtime_metadata=describe_model_runtime(
                    model, role="notes_formatter"
                ),
            )
        return result

    result = await _agent_run(initial_prompt)
    if activity is not None:
        activity.status("working", "Validating formatting against saved content…")
    err, screened, stage = _screen_patch(_output_json(result.output), sheet)
    if err is not None and stage in ("parse", "sheet"):
        # Mechanically rejected output gets one bounded correction attempt.
        logger.warning(
            "notes formatter output rejected run=%s sheet=%s stage=%s error=%s",
            run_id, sheet, stage, err.get("error"),
        )
        retry_result = await _agent_run(
            _build_output_rejected_prompt(sheet, _output_json(result.output), err["error"]),
        )
        retry_err, retry_screened, _retry_stage = _screen_patch(
            _output_json(retry_result.output), sheet, revised=True,
        )
        if retry_err is not None:
            return err  # keep the ORIGINAL rejection — it names the root cause
        err, screened = None, retry_screened
    if err is not None:
        return err
    patch, summary = screened.patch, screened.summary

    # Validate each notes cell independently, retaining all operations for a
    # row as one unit. A bad target must not discard unrelated valid notes.
    repair_attempted = False
    layouts_by_row: dict[int, dict] = {}
    reports = list(patch.get("unresolved") or [])

    def _keep_layouts(layout: dict, accepted: dict) -> None:
        rows_ok = {cell["row"] for cell in accepted["cells"]}
        layouts_by_row.update({note["row"]: note for note in layout.get("notes") or []
                               if isinstance(note, dict) and note.get("row") in rows_ok})

    try:
        layout = patch
        patch, rejected_patch, row_errors = _partition_layout(rows_for_patch, layout, padding)
        _keep_layouts(layout, patch)
    except FormatPatchError as exc:
        # No trustworthy row identity: repair the whole patch once, without
        # coercing model-authored row numbers into writable targets.
        failure = {
            "ok": False, "error": str(exc), "error_type": "validation_failed",
            "summary": summary or "No safe formatting changes were applied.",
            "patch": patch, "changed_rows": 0,
        }
        repair_attempted = True
        if activity is not None:
            activity.status("working", "Repairing rejected formatting decisions…")
        repair_result = await _agent_run(_build_validation_repair_prompt(
            sheet, patch, str(exc), rows_for_patch,
        ))
        err, screened, _stage = _screen_patch(
            _output_json(repair_result.output), sheet, revised=True,
        )
        if err is not None:
            return failure
        try:
            patch, rejected_patch, row_errors = _partition_layout(
                rows_for_patch, screened.patch, padding,
            )
        except FormatPatchError:
            return failure
        _keep_layouts(screened.patch, patch)
        if not apply_sheet_patch(rows_for_patch, patch).changed_rows:
            return failure
        summary = screened.summary
        reports += screened.patch.get("unresolved") or []
    if row_errors and not repair_attempted:
        if activity is not None:
            activity.status("working", "Repairing rejected formatting decisions…")
        original_error = "; ".join(row_errors.values())
        repair_result = await _agent_run(_build_validation_repair_prompt(
            sheet, rejected_patch, original_error,
            {row: html for row, html in rows_for_patch.items() if row in row_errors},
        ))
        err, screened, _stage = _screen_patch(
            _output_json(repair_result.output), sheet, revised=True,
        )
        if err is None:
            reports += [item for item in screened.patch.get("unresolved") or []
                        if isinstance(item, dict) and item.get("row") in row_errors]
            # The repair owns only rejected rows. It cannot replace previously
            # accepted decisions, nor silently style a different note.
            try:
                repaired, _, repair_errors = _partition_layout(
                    {row: html for row, html in rows_for_patch.items() if row in row_errors},
                    screened.patch, padding,
                )
            except FormatPatchError:
                repaired, repair_errors = {"cells": []}, {}
            _keep_layouts(screened.patch, repaired)
            for cell in repaired["cells"]:
                row = cell["row"]
                check = apply_sheet_patch(rows_for_patch, {"cells": [cell]})
                if check.changed_rows:
                    patch["cells"].append(cell)
                    row_errors.pop(row, None)
            row_errors.update({
                row: error for row, error in repair_errors.items() if row in row_errors
            })

    applied = apply_sheet_patch(rows_for_patch, patch)
    row_errors.update(missing_page_errors)
    # Notes the formatter says still differ from the source keep their
    # best-effort formatting; they are saved but stay failed for a person.
    layout_notes: dict[int, str] = {}
    reported_rows, miscopied = _reported_unresolved(reports, rows_for_patch, row_errors, layout_notes)
    for row in row_errors:
        if row in applied.rows and row not in reported_rows:
            applied.rows[row] = rows_for_patch[row]
    changed_count = sum(html != rows_for_patch[row] for row, html in applied.rows.items())
    skipped_rows: list[int] = []
    written_rows: list[int] = []
    cells_by_row = {cell.row: cell for cell in cells}
    with repo.db_session(db_path) as conn:
        # Take the write lock up front so the conditional writes + snapshot
        # below commit as one atomic unit (WAL + busy_timeout make concurrent
        # writers wait, not fail).
        conn.execute("BEGIN IMMEDIATE")
        live_cells = {c.row: c for c in list_formatter_cells_for_run(conn, run_id) if c.sheet == sheet}
        for row, html in sorted(applied.rows.items()):
            live = live_cells.get(row)
            launch = cells_by_row[row]
            # Re-check ownership under the write lock: a person may have saved
            # this note (even with identical HTML) after launch, and the HTML
            # compare-and-swap alone would then strip their ownership.
            if live is not None and live.style_source == "human":
                person_edited.append(row)
                row_errors.pop(row, None)
                miscopied.pop(row, None)
                continue
            if (live is None or live.html != rows_for_patch[row]
                or live.source_pages != launch.source_pages
                or live.source_generation_id != launch.source_generation_id):
                skipped_rows.append(row)
                continue
            if row in row_errors and row not in reported_rows:
                continue
            if html == rows_for_patch[row]:
                continue
            # Statement-atomic compare-and-swap (`WHERE html = ?`): only
            # overwrite the exact HTML this pass formatted. A row edited
            # since launch (user PATCH, reviewer fix) or deleted since
            # launch (sheet regenerate) fails the WHERE and is skipped —
            # never clobbered, never resurrected. The check lives IN the
            # UPDATE itself, so there is no read-then-write window.
            if repo.cas_update_notes_cell_html(
                conn, run_id=run_id, sheet=sheet, row=row,
                expected_html=rows_for_patch[row], new_html=html,
                # This pass just styled the cell — stamp it so any stale
                # "unstyled"/"floor" chip clears (schema v29).
                style_source="formatter",
            ):
                written_rows.append(row)
            else:
                skipped_rows.append(row)
        if written_rows:
            # Snapshot the pre-format HTML of exactly the rows written, in
            # the SAME transaction — "Revert formatting" restores from here
            # (schema v27; safety is versioning).
            repo.save_notes_format_snapshots(
                conn, run_id, sheet,
                {row: rows_for_patch[row] for row in written_rows},
                pass_started_at=pass_started_at,
            )
        written = len(written_rows)

    for row in skipped_rows:
        row_errors[row] = "Note changed during formatting; retry its current content."
        miscopied.pop(row, None)
    # A note on house style describes formatting that was saved.
    layout_notes = {row: note for row, note in layout_notes.items()
                    if row in applied.rows and row not in skipped_rows and row not in person_edited
                    and row not in row_errors}
    if row_errors:
        summary_out = (
            f"Formatting saved for {written} row(s); "
            f"{len(row_errors)} row(s) remain unresolved: "
            + ", ".join(str(row) for row in sorted(row_errors)) + "."
        )
    else:
        summary_out = summary or ("No formatting changes needed." if changed_count == 0
                                  else "Formatting applied.")
    if skipped_rows:
        summary_out += f" {len(skipped_rows)} row(s) skipped — edited during formatting."
    if person_edited:
        summary_out += f" {len(person_edited)} person-edited row(s) left unchanged."
    if layout_notes:
        summary_out += (f" {len(layout_notes)} note(s) use the nearest house style where the "
                        "source formatting cannot be drawn.")
    return {
        "ok": not row_errors, "summary": summary_out,
        **({"layout_notes": layout_notes} if layout_notes else {}),
        **({"miscopied": miscopied} if miscopied else {}),
        **({"error_type": "visual_review_failed" if reported_rows & set(row_errors) else "validation_failed",
            "error": "; ".join(row_errors.values()),
            "failed_rows": sorted(row_errors), "row_errors": row_errors}
           if row_errors else {}),
        "changed_rows": written,
        "skipped_rows": skipped_rows,
        **({"person_edited_rows": sorted(person_edited)} if person_edited else {}),
        "patch": patch, "layouts": {str(row): note for row, note in sorted(layouts_by_row.items())},
        "before_text_hash": applied.before_text_hash,
        "after_text_hash": applied.after_text_hash,
    }


def _reported_unresolved(
    reports: list[Any], rows: dict[int, str], row_errors: dict[int, str],
    layout_notes: dict[int, str] | None = None,
) -> tuple[set[int], dict[int, list[tuple[int, str]]]]:
    """Record the formatter's own unresolved notes in ``row_errors``.

    A ``house_style`` report is not a failure: it goes to ``layout_notes`` as
    a note for the person. Returns the reported rows and, per row, the
    ``(table, reason)`` pairs to re-copy. A miscopied-table report triggers a
    re-copy only when its quote is text from that table; otherwise it is an
    ordinary finding.
    """
    reported: set[int] = set()
    miscopied: dict[int, list[tuple[int, str]]] = {}
    for item in reports:
        try:
            report = UnresolvedNote.model_validate(item)
        except ValueError:
            # An unreadable report still names a note the formatter flagged.
            row = item.get("row") if isinstance(item, dict) else None
            if type(row) is not int or row not in rows:
                continue
            report = UnresolvedNote(row=row, kind="cannot_format",
                                    reason="The formatter flagged this note without a readable reason.")
        if report.row not in rows:
            continue
        where = f"Table {report.table}: " if report.table is not None else ""
        if report.kind == "house_style":
            if layout_notes is not None:
                previous = layout_notes.get(report.row)
                layout_notes[report.row] = (f"{previous}; " if previous else "") + where + report.reason
            continue
        reported.add(report.row)
        previous = row_errors.get(report.row)
        row_errors[report.row] = (f"{previous}; " if previous else "") + where + report.reason
        if report.kind != "miscopied_table" or report.table is None:
            continue
        tables = BeautifulSoup(rows[report.row], "html.parser").find_all("table")
        # A quote copied from the outline may carry its shortening marker.
        quote = report.quote.removesuffix("…[shortened]").strip()
        quote = " ".join(BeautifulSoup(quote, "html.parser").get_text(" ").split()).casefold()
        if report.table < len(tables) and quote and quote in " ".join(
                tables[report.table].get_text(" ", strip=True).split()).casefold():
            miscopied.setdefault(report.row, []).append((report.table, report.reason))
    return reported, miscopied


def _partition_layout(
    current_rows: dict[int, str], layout: dict[str, Any],
    cell_padding: tuple[int, int] = (3, 3),
) -> tuple[dict[str, Any], dict[str, Any], dict[int, str]]:
    """Compile each note row's layout independently into patch operations.

    Returns ``(patch, rejected_layout, row_errors)``. A row whose layout does
    not fit its live tables is rejected whole with specific messages; valid
    rows proceed. Raises FormatPatchError only when row identity is unusable.
    """
    notes = layout.get("notes")
    if not isinstance(notes, list):
        raise FormatPatchError("layout.notes must be a list")
    grouped: dict[int, list[Any]] = {}
    for note in notes:
        if not isinstance(note, dict) or type(note.get("row")) is not int:
            raise FormatPatchError("each note layout needs an integer row")
        grouped.setdefault(note["row"], []).append(note)
    accepted, rejected, errors = [], [], {}
    for row, entries in grouped.items():
        try:
            if len(entries) > 1:
                raise FormatPatchError("the row is described more than once")
            if row not in current_rows:
                raise FormatPatchError(f"row {row} is not a filled notes cell")
            try:
                note = NoteLayout.model_validate(entries[0])
            except ValueError as exc:
                raise FormatPatchError(f"invalid layout: {exc}") from exc
            ops = layout_operations(current_rows[row], note.tables, cell_padding)
            ops += [op.model_dump(exclude_none=True) for op in note.blocks]
            cell = {"row": row, "operations": ops}
            apply_sheet_patch(current_rows, {"cells": [cell]})
        except (FormatPatchError, LayoutError) as exc:
            rejected.extend(entries)
            errors[row] = f"row {row}: {exc}"
        else:
            accepted.append(cell)
    return ({"cells": accepted}, {**layout, "notes": rejected}, errors)


def collect_size_signals(
    cells: list[Any], theme: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Deterministic mTool size signals for the formatter agent (handoff
    item 2). Re-runs the SAME full → compact → lite → flat → oversize ladder
    the mTool exporter uses (:func:`mtool.notes_exporter._resolve_note_html`,
    sized against Excel's 32,767-char cell limit with the run's resolved
    theme), so the agent is HANDED the verdicts instead of re-deriving size
    math — deterministic code owns what-fits, the agent owns what-to-do.

    Returns one entry per flagged cell: ``{row, label, tier}`` with tier in
    ``lite`` / ``flat`` / ``oversize``. Unflagged cells are omitted — ``full``
    needs no size attention and neither does ``compact`` (same visible
    formatting, slimmer styling; docs/PLAN-mtool-compact-decoration.md).
    """
    style = NotesTableStyle.from_theme(theme)
    signals: list[dict[str, Any]] = []
    for c in cells:
        try:
            # The trailing elements (source styling / white grid dropped for
            # size) are fill-report provenance, not size verdicts — no remedy
            # here.
            _html, tier, _destyled, _grid = _resolve_note_html(
                c.html or "", style, True)
        except Exception:  # noqa: BLE001 — signals are advisory; never block
            logger.warning(
                "size-signal computation failed for row %s", c.row, exc_info=True,
            )
            continue
        if tier in ("lite", "flat", "oversize"):
            signals.append({"row": c.row, "label": c.label, "tier": tier})
    return signals


# Remedy text per tier — the division of labour the prompt enforces: the
# agent judges WHAT to simplify/split; it never re-derives WHETHER it fits.
_TIER_REMEDY = {
    "oversize": (
        "SKIPPED by the mTool fill — too big even with no styling. Remedy: "
        "the CONTENT must be split into smaller notes. You cannot fix this "
        "with styling and must not try; describe the recommended split in "
        "format_summary."
    ),
    "flat": (
        "will export to the mTool copy with ALL styling dropped so it fits "
        "Excel's cell limit. This degradation is AUTOMATIC and applies ONLY "
        "to the mTool export — the editor/review copy keeps full formatting. "
        "Do NOT remove styling from the note to compensate; match the source "
        "PDF exactly as you would for any other note."
    ),
    "lite": (
        "will export to the mTool copy with reduced (lite) styling. AUTOMATIC "
        "and mTool-only — the editor/review copy is unchanged. No action "
        "required; do NOT remove styling from the note."
    ),
}


def _size_signals_block(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return ""
    lines = [
        "\n\nSIZE SIGNALS (deterministic — computed by code against Excel's "
        "32,767-character cell limit; do NOT re-derive or dispute sizes):"
    ]
    for s in signals:
        lines.append(
            f"- row {s['row']} ({s['label']}): {s['tier'].upper()} — "
            f"{_TIER_REMEDY[s['tier']]}"
        )
    counts = {t: sum(1 for s in signals if s["tier"] == t)
              for t in ("lite", "flat", "oversize")}
    lines.append(
        f"Counts: formatting_reduced={counts['lite']}, "
        f"formatting_dropped={counts['flat']}, oversize={counts['oversize']}."
    )
    return "\n".join(lines)


def _build_user_prompt(
    sheet: str, cells: list[Any], pages: list[int],
    size_signals: Optional[list[dict[str, Any]]] = None,
    *,
    source_pages_attached: bool = False,
    appearance: Optional[dict[str, Any]] = None,
) -> str:
    compact_rows = []
    for c in cells:
        compact_rows.append({
            "row": c.row, "label": c.label, "html": c.html,
            "evidence": c.evidence, "source_pages": c.source_pages,
            # Grid columns per cell: the coordinates a layout uses. Word
            # source-styled tables are omitted and need no layout.
            "layout_tables": layout_manifest(c.html),
        })
    source_instruction = (
        f"Source page images {pages} are attached below. Do not call "
        "view_pdf_pages for those pages again; use zoom_pdf_region only when "
        "fine border or alignment detail is illegible."
        if source_pages_attached else
        f"First call view_pdf_pages for these source pages: {pages}."
    )
    return (
        f"Format sheet {sheet!r}. {source_instruction} Draft the layouts, check "
        "them with preview_layout, then return one layout for this sheet only."
        f"{_size_signals_block(size_signals or [])}\n\n"
        "RESOLVED OUTPUT APPEARANCE: " + json.dumps(appearance or firm_theme()) + "\n"
        "Describe every table listed in each row's layout_tables, using its grid columns. "
        f"CURRENT CELLS:\n{json.dumps(compact_rows, ensure_ascii=False)}"
    )


async def _preload_source_pages(
    pdf_path: str,
    pages: list[int],
) -> tuple[list[Union[str, BinaryContent]], set[int]]:
    """Render the formatter's known source pages before its first request.

    This is intentionally all-or-nothing: if one source page cannot render,
    the caller falls back to the existing ``view_pdf_pages`` tool path rather
    than claiming a partial attachment set is complete.
    """
    if not pages:
        return [], set()
    try:
        from notes.agent import (
            _NOTES_RENDER_DPI,
            _NOTES_RENDER_POLICY,
            _render_one_page_single_flight,
        )

        rendered = await asyncio.gather(*(
            _render_one_page_single_flight(
                pdf_path,
                page,
                _NOTES_RENDER_DPI,
                policy=_NOTES_RENDER_POLICY,
            )
            for page in pages
        ))
    except Exception:  # noqa: BLE001 - retain the tool-based fallback path
        logger.warning(
            "Could not preload formatter source pages; falling back to the "
            "view_pdf_pages tool.",
            exc_info=True,
        )
        return [], set()

    content: list[Union[str, BinaryContent]] = []
    for page, png in zip(pages, rendered):
        content.extend([
            f"=== Page {page} ===",
            BinaryContent(data=png, media_type="image/png"),
        ])
    return content, set(pages)


def _build_validation_repair_prompt(
    sheet: str,
    layout: dict[str, Any],
    error: str,
    rows_for_patch: dict[int, str],
) -> str:
    coordinates = {row: layout_manifest(html) for row, html in sorted(rows_for_patch.items())}
    return (
        "Your previous layout did not fit the live tables and was not saved. "
        "Return one corrected SheetLayout for these rows only. Each row needs "
        "exactly one layout per table in its coordinates, one column role per "
        "grid column, and lines whose columns start and end on whole cells. If "
        "you cannot describe a table safely, omit that row; it stays unresolved. "
        "Do not change content.\n\n"
        f"SHEET: {sheet}\n"
        f"VALIDATION ERROR: {error}\n"
        f"FAILED LAYOUT:\n{json.dumps(layout, ensure_ascii=False)}\n\n"
        f"LAYOUT COORDINATES BY ROW:\n{json.dumps(coordinates, ensure_ascii=False)}"
    )


def _build_output_rejected_prompt(
    sheet: str, rejected_output: str, error: str,
) -> str:
    snippet = rejected_output.strip()
    if len(snippet) > 4000:
        snippet = snippet[:4000] + " …[truncated]"
    return (
        "Your previous response was rejected before it could be validated:\n"
        f"REJECTION: {error}\n\n"
        "Return the SAME formatting decisions as ONE raw JSON object using the "
        "SheetLayout schema — no prose, no Markdown fences, nothing before or after "
        f"the JSON. The layout's \"sheet\" must be {sheet!r}.\n\n"
        f"YOUR REJECTED RESPONSE:\n{snippet}"
    )


def _resolve_notes_table_theme(db_path: str, run_id: int, *, conn=None) -> dict[str, Any]:
    """The RESOLVED notes-table style theme for a run: the run's own override
    (a full snapshot — schema v22) wins, else the firm theme.

    The firm layer resolves through ``notes.table_theme.firm_theme`` — the SAME
    resolver ``server._notes_table_style`` uses. It used to re-parse the env var
    here and fall back to ``{}``; once the shipped firm default became the
    accountant-ruled house style, that made the agent reason about a boxed grey
    grid while the panel rendered borderless, so it would "correct" formatting
    that was already right. Mirrors ``resolveTheme`` in
    web/src/lib/clipboardFormat.ts for the mTool size signals."""
    try:
        with nullcontext(conn) if conn is not None else repo.db_session(db_path) as theme_conn:
            run = repo.fetch_run(theme_conn, run_id)
        override = getattr(run, "notes_table_style", None) if run else None
        if isinstance(override, dict) and override:
            return resolve_run_theme(override)
    except Exception:  # noqa: BLE001 — theme is advisory; never fail the pass
        logger.warning(
            "could not read run notes_table_style run=%s", run_id, exc_info=True,
        )
    return firm_theme()


def _parse_json_patch(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Models routinely wrap the patch in prose ("Here is the patch: {…}")
        # despite the JSON-only instruction. Before rejecting, try to extract
        # the first balanced top-level JSON object from the text — this rescues
        # the most common failure shape without another model round-trip.
        obj = _extract_json_object(raw)
        if obj is None:
            raise FormatPatchError(
                f"formatter returned invalid JSON: {exc}"
            ) from exc
    if not isinstance(obj, dict):
        raise FormatPatchError("formatter output must be a JSON object")
    return obj


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """First parseable balanced ``{…}`` object in ``text``, or None. Tracks
    string/escape state so braces inside JSON strings don't break the scan."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break  # malformed candidate — try the next "{"
                    return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


def _table_geometry(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, Any]] = []
    for table_idx, table in enumerate(soup.find_all("table")):
        if not isinstance(table, Tag):
            continue
        rows = _direct_table_rows(table)
        row_items: list[dict[str, Any]] = []
        for row_idx, tr in enumerate(rows, start=1):
            cells = [
                c for c in tr.find_all(["th", "td"], recursive=False)
                if isinstance(c, Tag)
            ]
            row_items.append({
                "r": row_idx,
                "cell_count": len(cells),
                "cells": [
                    {
                        "c": cell_idx,
                        "tag": cell.name,
                        "rowspan": cell.get("rowspan") or "1",
                        "colspan": cell.get("colspan") or "1",
                        "text": cell.get_text(" ", strip=True)[:80],
                    }
                    for cell_idx, cell in enumerate(cells, start=1)
                ],
            })
        out.append({"table": table_idx, "row_count": len(rows), "rows": row_items})
    return out
