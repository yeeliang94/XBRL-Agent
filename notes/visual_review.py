"""Independently check prepared notes table appearance against the source PDF.

The reviewer reads the same prepared HTML used by review and Copy, plus its
table geometry, beside source page images. An incomplete assessment is
unresolved, not a successful check.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage, UsageLimits

from agent_tracing import save_messages_trace
from mtool.notes_exporter import prepare_note_output
from model_settings import describe_model_runtime
from notes.format_patch import FormatPatchError, apply_sheet_patch

logger = logging.getLogger(__name__)

# Shared by the initial check and one recheck, separately from formatting.
MAX_VISUAL_REVIEW_REQUESTS = 8


class TableAssessment(BaseModel):
    row: int = Field(ge=1)
    table: int = Field(ge=0)
    status: Literal["pass", "needs_correction", "unresolved"]
    source_pages: list[int]
    reason: str = Field(min_length=1)


class VisualReview(BaseModel):
    tables: list[TableAssessment]


def create_visual_review_agent(*, run_id, db_path, pdf_path, sheet, model):
    from notes.formatting_agent import _create_notes_style_agent

    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "notes_visual_review.md").read_text(
        encoding="utf-8",
    ).strip()
    return _create_notes_style_agent(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
        model=model, prompt=prompt, output_type=VisualReview,
        cache_key="xbrl-notes-visual-review",
    )


def _add_error(errors: dict[int, str], row: int, finding: str) -> None:
    previous = errors.get(row)
    errors[row] = f"{previous}; {finding}" if previous else finding


def prepare_note_evidence(html: str, style) -> tuple[str, dict]:
    """Return the prepared output HTML used by review and Copy, with its hash."""
    prepared = prepare_note_output(html, style=style, decorate=True)
    if prepared["tier"] == "oversize":
        raise ValueError("Prepared note exceeds the output size limit; visual checking is unresolved.")
    return prepared["html"], {
        "html_sha256": hashlib.sha256(prepared["html"].encode()).hexdigest(),
        "tier": prepared["tier"],
    }


def note_verification_identity(html: str, style, source_pages: list[int], source_generation_id=None) -> dict:
    """Bind a successful check to canonical content, prepared output and evidence."""
    _, evidence = prepare_note_evidence(html, style)
    return {
        "canonical_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "output_sha256": evidence["html_sha256"],
        "source_pages": sorted(set(source_pages)),
        "source_generation_id": source_generation_id,
    }


def stale_verified_rows(conn, run_id: int, sheet: str, result: dict) -> list[int]:
    """Later edits, source changes and output-setting changes invalidate a receipt."""
    from db import repository as repo
    from mtool.notes_decorate import NotesTableStyle
    from notes.formatting_agent import _resolve_notes_table_theme

    receipts = result.get("verified_rows") or {}
    style = NotesTableStyle.from_theme(_resolve_notes_table_theme("", run_id, conn=conn))
    cells = {c.row: c for c in repo.list_formatter_cells_for_run(conn, run_id) if c.sheet == sheet}
    stale = []
    for raw_row, receipt in receipts.items():
        row = int(raw_row)
        cell = cells.get(row)
        try:
            current = note_verification_identity(cell.html, style, cell.source_pages, cell.source_generation_id) if cell else None
        except ValueError:
            current = None
        if current != receipt:
            stale.append(row)
    return sorted(stale)


def retain_other_verification_receipts(previous: dict, current: dict) -> dict:
    """A targeted retry replaces its own decisions and keeps unrelated evidence."""
    receipts = {str(row): identity for row, identity in (previous.get("verified_rows") or {}).items()}
    for row in [*(current.get("failed_rows") or []), *(current.get("skipped_rows") or [])]:
        receipts.pop(str(row), None)
    receipts.update({str(row): identity for row, identity in (current.get("verified_rows") or {}).items()})
    return {**current, "verified_rows": receipts}


async def check_visual_rows(*, rows, cells, style, run_id, db_path, pdf_path,
                            sheet, model, usage, limits, output_dir, trace_label):
    """A fresh context on every check; no formatter conversation is evidence."""
    from notes.formatting_agent import _preload_source_pages, _table_geometry

    by_row = {cell.row: cell for cell in cells}
    expected = {(row, index) for row, html in rows.items()
                for index, _ in enumerate(BeautifulSoup(html, "html.parser").find_all("table"))}
    if not expected:
        return {}, {"tables": [], "evidence": {}}
    errors, evidence, content_by_row = {}, {}, {}
    for row in sorted({row for row, _ in expected}):
        try:
            prepared, identity = prepare_note_evidence(rows[row], style)
            evidence[row] = identity
            content_by_row[row] = (
                f"FORMATTED NOTE ROW {row}; geometry: " + json.dumps(_table_geometry(rows[row]))
                + "\nPREPARED OUTPUT HTML:\n" + prepared
            )
        except ValueError as exc:
            errors[row] = str(exc)
    reviewable = {key for key in expected if key[0] not in errors}
    if not reviewable:
        return errors, {"tables": [], "evidence": evidence}
    source_pages = sorted({p for row, _ in reviewable for p in by_row[row].source_pages})
    # Load each unique page independently, using the existing shared renderer.
    # A failed continuation page blocks its note, not another note in the batch.
    page_results = await asyncio.gather(*(
        _preload_source_pages(pdf_path, [page]) for page in source_pages
    ))
    source_by_page = {
        page: content for page, (content, loaded) in zip(source_pages, page_results)
        if page in loaded
    }
    for row in {row for row, _ in reviewable}:
        required_pages = set(by_row[row].source_pages)
        if not required_pages or not required_pages.issubset(source_by_page):
            errors[row] = "Source PDF images are incomplete; visual comparison is unresolved."
    reviewable = {key for key in reviewable if key[0] not in errors}
    if not reviewable:
        return errors, {"tables": [], "evidence": evidence}
    attached = {page for row, _ in reviewable for page in by_row[row].source_pages}
    source = [part for page in sorted(attached) for part in source_by_page[page]]
    content = [content_by_row[row] for row in sorted({row for row, _ in reviewable})]
    agent, deps = create_visual_review_agent(
        run_id=run_id, db_path=db_path, pdf_path=pdf_path, sheet=sheet,
        model=model,
    )
    deps.viewed_pages.update(attached)
    manifest = [{"row": row, "table": table, "source_pages": by_row[row].source_pages}
                for row, table in sorted(reviewable)]
    try:
        result = await agent.run(
            ["Check ALL manifest tables against the source PDF images. Manifest: "
             + json.dumps(manifest), *content, *source], deps=deps,
            usage=usage, usage_limits=limits,
        )
        if output_dir:
            save_messages_trace(result.all_messages(), output_dir, trace_label,
                                runtime_metadata=describe_model_runtime(model, role="notes_formatter"))
        decision = VisualReview.model_validate(result.output)
    except UsageLimitExceeded:
        logger.warning("Visual reviewer exhausted its request budget run=%s sheet=%s", run_id, sheet)
        for row in {row for row, _ in reviewable}:
            _add_error(errors, row, "Visual checking reached its request budget; assessment is unresolved.")
        return errors, {"tables": [], "evidence": evidence, "error_type": "turn_budget"}
    except Exception:
        logger.warning(
            "Visual reviewer failed run=%s sheet=%s", run_id, sheet, exc_info=True,
        )
        for row, _ in reviewable:
            errors[row] = "Visual reviewer did not complete a valid assessment."
        return errors, {"tables": [], "evidence": evidence}
    seen = set()
    for assessment in decision.tables:
        key = (assessment.row, assessment.table)
        if key not in reviewable or key in seen:
            # An ambiguous output cannot establish which tables were checked.
            for row in {row for row, _ in reviewable}:
                _add_error(errors, row, "Visual assessment contained duplicate or unexpected tables.")
            continue
        seen.add(key)
        issue = None
        if not assessment.source_pages:
            issue = "Visual assessment did not cite a source page."
        elif not set(assessment.source_pages).issubset(deps.viewed_pages):
            issue = "Visual assessment cited unseen source pages."
        elif not set(assessment.source_pages).issubset(by_row[assessment.row].source_pages):
            issue = "Visual assessment cited pages outside this note's source pages."
        elif assessment.status != "pass":
            issue = assessment.reason
        if issue:
            finding = f"Table {assessment.table}: {issue}"
            _add_error(errors, assessment.row, finding)
    for row, _ in reviewable - seen:
        _add_error(errors, row, "Visual assessment omitted a table.")
    return errors, {**decision.model_dump(), "evidence": evidence}


async def review_and_correct_rows(
    *, rows: dict[int, str], cells, style, run_id: int, db_path: str,
    pdf_path: str, sheet: str, model, usage: RunUsage,
    formatter_run: Callable[[Any], Awaitable[Any]], output_dir: str,
    trace_label: str, on_phase: Callable[[str], None] | None = None,
) -> tuple[dict[int, str], dict, dict[int, str]]:
    """Keep passed rows when a later correction or check cannot finish."""
    from notes.formatting_agent import _output_json, _partition_valid_patch, _screen_patch, _table_geometry, _allowed_targets_from_geometry

    review_usage = RunUsage()
    review_limits = UsageLimits(request_limit=MAX_VISUAL_REVIEW_REQUESTS)

    def phase(message: str) -> None:
        if on_phase is not None:
            try:
                on_phase(message)
            except Exception:
                logger.warning("Could not publish formatter phase", exc_info=True)

    async def check(candidate_rows, suffix):
        return await check_visual_rows(
            rows=candidate_rows, cells=cells, style=style,
            run_id=run_id, db_path=db_path, pdf_path=pdf_path,
            sheet=sheet, model=model, usage=review_usage, limits=review_limits,
            output_dir=output_dir, trace_label=trace_label + suffix,
        )

    try:
        phase("Checking formatted notes against the source PDF…")
        errors, review = await check(rows, "_visual")
        correctable = {item["row"] for item in review.get("tables", [])
                       if item["status"] == "needs_correction" and item["row"] in errors}
        if not correctable:
            return errors, review, {}

        phase("Correcting table formatting from the visual findings…")
        failed_current = {row: rows[row] for row in correctable}
        try:
            correction = await formatter_run(
                "VISUAL REVIEW FAILED. Return a style-only SheetFormatPatch for only these rows. "
                "Correct the reported edges against the source PDF; do not change text or geometry. "
                "Coordinates refer to the current candidate HTML below. Match each edge to its row label "
                "and both amount cells; do not infer row numbers from the source image. Findings: "
                + json.dumps({row: errors[row] for row in correctable})
                + "\nCOORDINATE MANIFEST: " + json.dumps({row: {
                    "geometry": _table_geometry(html),
                    "allowed_targets": _allowed_targets_from_geometry(_table_geometry(html)),
                } for row, html in failed_current.items()})
                + "\nCURRENT CANDIDATES: " + json.dumps(failed_current),
            )
            correction_error, screened, _ = _screen_patch(_output_json(correction.output), sheet, revised=True)
            if correction_error is not None:
                raise FormatPatchError(correction_error["error"])
            if any(cell.get("row") not in failed_current for cell in screened.patch["cells"]):
                raise FormatPatchError("Visual correction targeted an unreviewed row")
            safe, _, correction_errors = _partition_valid_patch(failed_current, screened.patch)
            for row, error in correction_errors.items():
                logger.warning("Visual correction rejected run=%s sheet=%s row=%s error=%s",
                               run_id, sheet, row, error)
            review["correction_patch"] = safe
            corrected = apply_sheet_patch(failed_current, safe)
            phase("Rechecking corrected notes against the source PDF…")
            remaining, recheck = await check(corrected.rows, "_visual_recheck")
            review["recheck"] = recheck
            # Keep initial findings in review history; current errors describe
            # only the candidate that was actually rechecked.
            for row in correctable:
                errors.pop(row, None)
                if row in remaining or row in correction_errors:
                    if row in remaining:
                        errors[row] = remaining[row]
                    if row in correction_errors:
                        _add_error(errors, row, correction_errors[row])
                else:
                    errors.pop(row, None)
            return errors, review, {row: html for row, html in corrected.rows.items() if row not in errors}
        except UsageLimitExceeded:
            logger.warning("Visual correction exhausted formatter budget run=%s sheet=%s", run_id, sheet)
            review["correction_error"] = "Formatting correction reached its request budget."
        except FormatPatchError as exc:
            logger.warning("Visual correction rejected run=%s sheet=%s error=%s", run_id, sheet, exc)
            review["correction_error"] = str(exc)
        except Exception:
            logger.warning("Visual correction failed run=%s sheet=%s", run_id, sheet, exc_info=True)
            review["correction_error"] = "Formatting correction did not complete."
        for row in correctable:
            _add_error(errors, row, review["correction_error"])
        return errors, review, {}
    finally:
        # Aggregate telemetry only after correction; checker requests cannot
        # consume the formatter's allowance while it is still running.
        usage.incr(review_usage)
