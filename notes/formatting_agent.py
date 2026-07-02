"""AI notes formatting pass for the Notes Review panel.

The model proposes a constrained JSON style patch. This module applies it
through notes.format_patch, which verifies content preservation before any DB
write.
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from bs4 import BeautifulSoup, Tag
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from agent_tracing import save_messages_trace
from db import repository as repo
from notes.format_patch import (
    FormatPatchError,
    apply_sheet_patch,
    describe_effective_appearance,
)
from model_settings import build_model_settings
from tools.pdf_viewer import count_pdf_pages, render_pages_to_png_bytes

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "notes_formatter.md"


def _resolve_min_confidence() -> float:
    """Patch-confidence floor, operator-tunable via
    XBRL_NOTES_FORMATTER_MIN_CONFIDENCE (validated + clamped to [0, 1])."""
    default = 0.70
    raw = os.environ.get("XBRL_NOTES_FORMATTER_MIN_CONFIDENCE", "")
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        logger.warning(
            "XBRL_NOTES_FORMATTER_MIN_CONFIDENCE=%r is not a number; using %.2f",
            raw, default,
        )
        return default
    if not 0.0 <= v <= 1.0:
        clamped = min(max(v, 0.0), 1.0)
        logger.warning(
            "XBRL_NOTES_FORMATTER_MIN_CONFIDENCE=%.3f outside [0, 1]; "
            "clamping to %.2f", v, clamped,
        )
        return clamped
    return v


MIN_CONFIDENCE = _resolve_min_confidence()

# Cumulative per-click model-request budget across the formatter's (up to
# three) agent.run passes. Like the extraction MAX_AGENT_ITERATIONS cap, it
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

# Failure taxonomy persisted to notes_format_tasks.error_type (schema v27) —
# branch on these codes, not on the human-facing error prose. No CHECK
# constraint on the column (same rationale as runs.status).
FORMATTER_ERROR_TYPES = (
    "timeout",              # wall-clock cap (XBRL_NOTES_FORMATTER_WALLCLOCK_S)
    "turn_budget",          # cumulative request cap (UsageLimitExceeded)
    "low_confidence",       # patch confidence below MIN_CONFIDENCE
    "validation_failed",    # bad JSON / content-preservation gate refused it
    "wrong_sheet",          # patch targeted a different sheet
    "precondition_failed",  # no PDF / no filled cells / missing source pages
    "model_error",          # unexpected exception in the pass
    "restarted",            # server restarted while the pass was running
    "reverted",             # user reverted the pass's formatting
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


def create_notes_formatter_agent(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
) -> tuple[Agent[NotesFormatterDeps, str], NotesFormatterDeps]:
    deps = NotesFormatterDeps(
        run_id=run_id, db_path=str(db_path), pdf_path=pdf_path,
        sheet=sheet, model=model,
    )
    base_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    agent = Agent(
        model,
        deps_type=NotesFormatterDeps,
        system_prompt=base_prompt,
        model_settings=build_model_settings(model, cache_key="xbrl-notes-formatter"),
    )

    @agent.tool
    def view_pdf_pages(
        ctx: RunContext[NotesFormatterDeps], pages: list[int],
    ) -> list[Union[str, BinaryContent]]:
        """View specific source PDF pages as images."""
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
    def read_note_cell(ctx: RunContext[NotesFormatterDeps], row: int) -> str:
        """Read one current notes cell HTML payload."""
        with repo.db_session(ctx.deps.db_path) as conn:
            cells = [
                c for c in repo.list_notes_cells_for_run(conn, ctx.deps.run_id)
                if c.sheet == ctx.deps.sheet and c.row == row
            ]
        if not cells:
            return f"{ctx.deps.sheet} row {row} is empty."
        c = cells[0]
        return json.dumps({
            "sheet": c.sheet, "row": c.row, "label": c.label,
            "html": c.html, "evidence": c.evidence,
            "source_pages": c.source_pages,
        }, ensure_ascii=False)

    return agent, deps


@dataclass(frozen=True)
class _ScreenedPatch:
    patch: dict[str, Any]
    confidence: float
    summary: str


def _screen_patch(
    output_text: str, sheet: str, *, revised: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[_ScreenedPatch], str]:
    """Run the gates every model output must pass before it may be applied:
    JSON parse, numeric confidence, confidence threshold, sheet match.

    Returns ``(error_return, screened, stage)`` where exactly one of
    ``error_return`` / ``screened`` is set and ``stage`` names the failing
    gate (``"parse" | "confidence" | "threshold" | "sheet" | "ok"``) so
    callers can special-case a parse failure (the repair pass keeps the
    original error; the self-check pass keeps the original patch).
    """
    prefix = "revised " if revised else ""
    try:
        patch = _parse_json_patch(output_text)
    except FormatPatchError as exc:
        return (
            {"ok": False, "error": str(exc), "error_type": "validation_failed"},
            None, "parse",
        )
    try:
        confidence = float(patch.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return (
            {
                "ok": False,
                "error": f"{prefix}formatter confidence must be numeric",
                "error_type": "validation_failed", "patch": patch,
            },
            None, "confidence",
        )
    summary = str(patch.get("format_summary") or "").strip()
    if confidence < MIN_CONFIDENCE:
        return (
            {
                "ok": False, "error": "formatter confidence below threshold",
                "error_type": "low_confidence",
                "summary": summary or "Formatter confidence below threshold.",
                "confidence": confidence, "patch": patch,
            },
            None, "threshold",
        )
    if patch.get("sheet") != sheet:
        return (
            {
                "ok": False,
                "error": f"{prefix}formatter patch targeted the wrong sheet",
                "error_type": "wrong_sheet",
            },
            None, "sheet",
        )
    return None, _ScreenedPatch(patch, confidence, summary), "ok"


def _usage_fields(usage: RunUsage) -> dict[str, int]:
    """Flatten the accumulated cross-pass usage into the task-row columns."""
    return {
        "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_write_tokens", 0) or 0),
    }


async def run_notes_formatter(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
    output_dir: str = "",
) -> dict[str, Any]:
    """Run the formatter pass and attach cross-pass token telemetry.

    The shared ``RunUsage`` accumulates across every ``agent.run`` inside the
    impl; flattening it here (one exit point) keeps the many early returns in
    the impl free of usage bookkeeping. Tokens are lost only when the pass
    raises (timeout / turn budget) — the API worker builds that outcome.
    """
    usage = RunUsage()
    outcome = await _run_notes_formatter_impl(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
        model=model, output_dir=output_dir, usage=usage,
    )
    outcome.update(_usage_fields(usage))
    return outcome


async def _run_notes_formatter_impl(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheet: str,
    model: Union[str, Model],
    output_dir: str,
    usage: RunUsage,
) -> dict[str, Any]:
    if not pdf_path or not Path(pdf_path).exists():
        return {
            "ok": False, "error": "source PDF is not available",
            "error_type": "precondition_failed",
        }

    with repo.db_session(db_path) as conn:
        cells = [
            c for c in repo.list_notes_cells_for_run(conn, run_id)
            if c.sheet == sheet and (c.html or "").strip()
        ]
    if not cells:
        return {
            "ok": False, "error": f"no filled prose cells found on {sheet}",
            "error_type": "precondition_failed",
        }

    missing_pages = [f"row {c.row}" for c in cells if not c.source_pages]
    if missing_pages:
        return {
            "ok": False,
            "error": "source pages are missing for " + ", ".join(missing_pages[:5]),
            "error_type": "precondition_failed",
        }

    rows_for_patch = {c.row: c.html for c in cells}
    page_set = sorted({p for c in cells for p in c.source_pages})
    prompt = _build_user_prompt(sheet, cells, page_set)

    agent, deps = create_notes_formatter_agent(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet, model=model,
    )
    # One shared usage accumulator + request cap across every pass below, so
    # the whole click (initial + repair + self-check) is bounded — not each
    # pass independently. UsageLimitExceeded surfaces as a structured "turn
    # budget" outcome in the API worker (mirrors the wall-clock timeout).
    limits = UsageLimits(request_limit=MAX_FORMATTER_REQUESTS)
    trace_messages: list = []

    async def _agent_run(user_prompt: str):
        result = await agent.run(
            user_prompt, deps=deps, usage=usage, usage_limits=limits,
        )
        # Re-write the trace after EVERY completed pass (best-effort, gotcha
        # #6): the trace is most valuable when a LATER pass times out or
        # errors — the completed passes are already on disk by then.
        if hasattr(result, "all_messages"):
            try:
                trace_messages.extend(result.all_messages())
            except Exception:  # noqa: BLE001
                pass
        if output_dir and trace_messages:
            save_messages_trace(
                trace_messages, output_dir, f"notes_format_{sheet}",
            )
        return result

    result = await _agent_run(prompt)
    err, screened, stage = _screen_patch(str(result.output), sheet)
    if err is not None and stage in ("parse", "confidence", "sheet"):
        # Mechanically rejected output (unparseable JSON / wrong sheet /
        # non-numeric confidence) — tell the model WHY and give it one retry,
        # mirroring the validation-repair pass below. Without this, a model
        # that formats well but wraps its answer in prose fails silently with
        # no feedback. A low-confidence patch (stage "threshold") is NOT
        # retried: that is the model's honest self-assessment, and re-asking
        # only pressures it to inflate the number.
        logger.warning(
            "notes formatter output rejected run=%s sheet=%s stage=%s error=%s",
            run_id, sheet, stage, err.get("error"),
        )
        retry_result = await _agent_run(
            _build_output_rejected_prompt(sheet, str(result.output), err["error"]),
        )
        retry_err, retry_screened, _retry_stage = _screen_patch(
            str(retry_result.output), sheet, revised=True,
        )
        if retry_err is not None:
            return err  # keep the ORIGINAL rejection — it names the root cause
        err, screened = None, retry_screened
    if err is not None:
        return err
    patch, confidence, summary = screened.patch, screened.confidence, screened.summary

    try:
        applied = apply_sheet_patch(rows_for_patch, patch)
    except FormatPatchError as exc:
        logger.warning(
            "notes formatter validation failed run=%s sheet=%s error=%s",
            run_id, sheet, exc,
        )
        repair_prompt = _build_validation_repair_prompt(
            sheet, patch, str(exc), rows_for_patch,
        )
        repair_result = await _agent_run(repair_prompt)
        err, screened, stage = _screen_patch(
            str(repair_result.output), sheet, revised=True,
        )
        if stage == "parse":
            # The repair didn't even parse — report the ORIGINAL validation
            # error; it is more actionable than "invalid JSON" from the retry.
            return {
                "ok": False, "error": str(exc),
                "error_type": "validation_failed", "summary": summary,
                "confidence": confidence, "patch": patch,
            }
        if err is not None:
            return err
        try:
            applied = apply_sheet_patch(rows_for_patch, screened.patch)
        except FormatPatchError as revised_exc:
            logger.warning(
                "notes formatter repaired patch validation failed run=%s sheet=%s error=%s",
                run_id, sheet, revised_exc,
            )
            return {
                "ok": False, "error": str(revised_exc),
                "error_type": "validation_failed",
                "summary": screened.summary,
                "confidence": screened.confidence, "patch": screened.patch,
            }
        patch, confidence, summary = (
            screened.patch, screened.confidence, screened.summary,
        )

    # One self-check revision pass: show the agent the sanitized preview HTML
    # that would be saved and let it either return the same patch or a revised
    # patch. The deterministic verifier still gates the final write.
    if applied.changed_rows:
        review_prompt = _build_self_check_prompt(sheet, patch, applied.rows)
        review_result = await _agent_run(review_prompt)
        err, screened, stage = _screen_patch(
            str(review_result.output), sheet, revised=True,
        )
        if stage == "parse":
            pass  # self-check output unparseable — keep the validated patch
        elif err is not None:
            return err
        elif screened.patch != patch:
            try:
                applied = apply_sheet_patch(rows_for_patch, screened.patch)
            except FormatPatchError as exc:
                logger.warning(
                    "notes formatter revised patch validation failed run=%s sheet=%s error=%s",
                    run_id, sheet, exc,
                )
                return {
                    "ok": False, "error": str(exc),
                    "error_type": "validation_failed",
                    "summary": screened.summary,
                    "confidence": screened.confidence, "patch": screened.patch,
                }
            patch, confidence, summary = (
                screened.patch, screened.confidence, screened.summary,
            )

    if applied.changed_rows == 0:
        return {
            "ok": True, "summary": summary or "No formatting changes needed.",
            "confidence": confidence, "changed_rows": 0, "skipped_rows": [],
            "patch": patch,
            "before_text_hash": applied.before_text_hash,
            "after_text_hash": applied.after_text_hash,
        }

    skipped_rows: list[int] = []
    written_rows: list[int] = []
    with repo.db_session(db_path) as conn:
        # Take the write lock up front so the conditional writes + snapshot
        # below commit as one atomic unit (WAL + busy_timeout make concurrent
        # writers wait, not fail).
        conn.execute("BEGIN IMMEDIATE")
        for row, html in sorted(applied.rows.items()):
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
            )
        written = len(written_rows)

    summary_out = summary or "Formatting applied."
    if skipped_rows:
        summary_out += (
            f" {len(skipped_rows)} row(s) skipped — edited during formatting."
        )
    return {
        "ok": True, "summary": summary_out,
        "confidence": confidence, "changed_rows": written,
        "skipped_rows": skipped_rows,
        "patch": patch, "before_text_hash": applied.before_text_hash,
        "after_text_hash": applied.after_text_hash,
    }


def _build_user_prompt(sheet: str, cells: list[Any], pages: list[int]) -> str:
    compact_rows = [
        {
            "row": c.row, "label": c.label, "html": c.html,
            "evidence": c.evidence, "source_pages": c.source_pages,
            "table_geometry": _table_geometry(c.html),
        }
        for c in cells
    ]
    return (
        f"Format sheet {sheet!r}. First call view_pdf_pages for these source "
        f"pages: {pages}. Then return one JSON patch for this sheet only.\n\n"
        f"CURRENT CELLS:\n{json.dumps(compact_rows, ensure_ascii=False)}"
    )


def _build_validation_repair_prompt(
    sheet: str,
    patch: dict[str, Any],
    error: str,
    rows_for_patch: dict[int, str],
) -> str:
    geometry = {
        row: _table_geometry(html) for row, html in sorted(rows_for_patch.items())
    }
    return (
        "Your previous formatter patch failed deterministic validation and was "
        "not saved. Return one full corrected JSON patch using only targets "
        "that exist in the current HTML. If you cannot safely map the source "
        "formatting to existing cells, return a no-op patch with cells: [] and "
        "a low-risk summary. Do not change content.\n\n"
        f"SHEET: {sheet}\n"
        f"VALIDATION ERROR: {error}\n"
        f"FAILED PATCH:\n{json.dumps(patch, ensure_ascii=False)}\n\n"
        f"CURRENT TABLE GEOMETRY BY ROW:\n"
        f"{json.dumps(geometry, ensure_ascii=False)}"
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
        "patch schema — no prose, no Markdown fences, nothing before or after "
        f"the JSON. The patch's \"sheet\" must be {sheet!r} and \"confidence\" "
        "must be a number.\n\n"
        f"YOUR REJECTED RESPONSE:\n{snippet}"
    )


def _build_self_check_prompt(
    sheet: str, patch: dict[str, Any], preview_rows: dict[int, str],
) -> str:
    # Effective rendered appearance, not raw HTML: the review panel paints a
    # default grey grid + grey header fill from theme CSS that is invisible in
    # the HTML, and models misread border EXTENT out of per-cell style soup
    # (the "double rule across the whole row instead of one column" failure).
    appearance = [
        {"row": row, "rendered_appearance": describe_effective_appearance(html)}
        for row, html in sorted(preview_rows.items())
    ]
    return (
        "Self-check your formatting patch against the original source pages you "
        "viewed. Below is the EFFECTIVE RENDERED APPEARANCE of every cell after "
        "your patch (explicit styles resolved, theme defaults shown where you "
        "set nothing). Check each rule's EXTENT: a border must span exactly the "
        "same cells as in the source — e.g. a summation rule under only the "
        "amount column must not run across the label column. Check fills the "
        "same way. If everything matches the source, return the same JSON "
        "patch. If borders/fills/alignment are wrong, return one revised JSON "
        "patch using the same schema. Do not change content.\n\n"
        f"SHEET: {sheet}\n"
        f"PATCH:\n{json.dumps(patch, ensure_ascii=False)}\n\n"
        f"RENDERED APPEARANCE BY ROW:\n"
        f"{json.dumps(appearance, ensure_ascii=False)}"
    )


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


def _direct_table_rows(table: Tag) -> list[Tag]:
    rows: list[Tag] = []
    for child in table.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "tr":
            rows.append(child)
        elif child.name in {"thead", "tbody", "tfoot"}:
            rows.extend(
                row for row in child.find_all("tr", recursive=False)
                if isinstance(row, Tag)
            )
    return rows
