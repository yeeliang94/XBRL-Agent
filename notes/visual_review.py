"""Independently check prepared notes table appearance against the source PDF.

The reviewer reads the same prepared HTML used by review and Copy, plus the
current canonical candidate and its geometry, beside source page images. An
incomplete assessment is unresolved, not a successful check.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
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
from notes.rule_map import rule_map_text

logger = logging.getLogger(__name__)

# Shared by the initial check and one recheck, separately from formatting.
MAX_VISUAL_REVIEW_REQUESTS = 8


SOURCE_DEFECT_KINDS = frozenset({"source_content", "source_provenance", "source_emphasis"})


class TableAssessment(BaseModel):
    row: int = Field(ge=1)
    table: int = Field(ge=0)
    status: Literal["pass", "needs_correction", "unresolved"]
    source_pages: list[int]
    reason: str = Field(min_length=1)
    observed_text: str = Field(
        default="",
        description="For any finding that is not a pass: an exact row label or cell "
                    "text copied from THIS table's canonical candidate, naming the row "
                    "the finding is about.",
    )
    content_defect: str = Field(
        default="",
        description="Added or missing words or figures in this table, quoted, when "
                    "present alongside another issue; empty otherwise.",
    )
    issue_kind: Literal["style", "structure", "source_content", "source_provenance",
                        "source_emphasis"] = Field(
        default="style",
        description='"structure" when the copied table itself differs from the PDF '
                    "(heading vs data cells, rows, columns, merged cells); source_content, "
                    "source_provenance or source_emphasis for wrong words/figures, wrong "
                    "source page or captured emphasis that must be removed; otherwise style.",
    )


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


def _layout_text(note: dict | None) -> str:
    from notes.table_layout import TableLayout, describe_layouts
    if not note:
        return "none"
    try:
        return "\n" + describe_layouts([TableLayout.model_validate(t) for t in note.get("tables") or []])
    except ValueError:
        return "unavailable"


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


def _table_packets(canonical: str, prepared: str) -> str | None:
    """Each table on its own: context, header line, candidate and prepared HTML.

    Adjacent current/comparative tables look alike in one blob (runs 183-185).
    Returns None when prepared output cannot be paired table by table.
    """
    source = BeautifulSoup(canonical, "html.parser")
    tables = source.find_all("table")
    prepared_tables = BeautifulSoup(prepared, "html.parser").find_all("table")
    if not tables or len(tables) != len(prepared_tables):
        return None
    outside = BeautifulSoup(canonical, "html.parser")
    for index, table in enumerate(outside.find_all("table")):
        table.replace_with(outside.new_string(f" [TABLE {index}] "))
    parts = ["NOTE TEXT OUTSIDE TABLES: " + " ".join(outside.get_text(" ", strip=True).split())[:1500]]
    for index, (table, output) in enumerate(zip(tables, prepared_tables)):
        context = next((
            " ".join(block.get_text(" ", strip=True).split())
            for block in table.find_all_previous(["p", "h1", "h2", "h3", "h4", "h5", "h6", "div"])
            if not block.find_parent("table") and not block.find("table")
            and block.get_text(strip=True)
        ), "")
        first_row = table.find("tr")
        header = " | ".join(
            " ".join(cell.get_text(" ", strip=True).split())
            for cell in (first_row.find_all(["td", "th"]) if first_row else [])
        )
        parts.append(
            f"TABLE {index} — preceding text: {context[:200] or 'none'}; first row: {header or 'empty'}"
            f"\nTABLE {index} CANONICAL CANDIDATE HTML:\n{table}"
            f"\nTABLE {index} PREPARED OUTPUT HTML:\n{output}"
        )
    return "\n".join(parts)


def structure_findings(outcome: dict) -> dict[int, list[tuple[int, str]]]:
    """Current structural findings per failed note row: (table, reason)."""
    reviews = outcome.get("visual_reviews") or [outcome.get("visual_review") or {}]
    failed = {int(row) for row in outcome.get("failed_rows") or []}
    current: dict[tuple[int, int], dict] = {}
    for review in reviews:
        for item in review.get("tables", []) + (review.get("recheck") or {}).get("tables", []):
            current[(item["row"], item["table"])] = item
    found: dict[int, list[tuple[int, str]]] = {}
    for (row, table), item in sorted(current.items()):
        if (row in failed and item.get("issue_kind") == "structure" and item.get("status") != "pass"
                and item.get("recopy_eligible", True)):
            found.setdefault(row, []).append((table, item.get("reason", "")))
    return found


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


def current_verification_cells(conn, run_id: int, sheet: str) -> dict:
    """Only filled, valid canonical disclosure fields need appearance evidence."""
    from db import repository as repo
    from notes.formatting_agent import formatter_cell_is_candidate

    return {c.row: c for c in repo.list_formatter_cells_for_run(conn, run_id)
            if c.sheet == sheet and formatter_cell_is_candidate(c, None)
            and BeautifulSoup(c.html, "html.parser").get_text().strip()}


def stale_verified_rows(conn, run_id: int, sheet: str, result: dict, *, cells=None) -> list[int]:
    """Later edits, source changes and output-setting changes invalidate a receipt."""
    from mtool.notes_decorate import NotesTableStyle
    from notes.formatting_agent import _resolve_notes_table_theme, formatter_cell_is_candidate
    from notes.auto_format import PDF_FORMAT_CANDIDATE_SOURCES

    receipts = result.get("verified_rows") or {}
    style = NotesTableStyle.from_theme(_resolve_notes_table_theme("", run_id, conn=conn))
    if cells is None:
        cells = current_verification_cells(conn, run_id, sheet)
    stale = []
    for row, cell in cells.items():
        receipt = receipts.get(str(row), receipts.get(row))
        if receipt is None:
            # Legacy tasks without receipts keep their existing eligibility.
            # A receipt-aware pass cannot certify a newly filled PDF field.
            if result.get("ok") is True and "verified_rows" in result and formatter_cell_is_candidate(
                cell, PDF_FORMAT_CANDIDATE_SOURCES | {None, "formatter"},
            ):
                stale.append(row)
            continue
        try:
            current = note_verification_identity(cell.html, style, cell.source_pages, cell.source_generation_id) if cell else None
        except ValueError:
            current = None
        if current != receipt:
            stale.append(row)
    return sorted(stale)


def retain_other_verification_receipts(previous: dict, current: dict, *, active_rows=None) -> dict:
    """A targeted retry replaces its own decisions and keeps unrelated evidence."""
    receipts = {str(row): identity for row, identity in (previous.get("verified_rows") or {}).items()}
    for row in [*(current.get("failed_rows") or []), *(current.get("skipped_rows") or [])]:
        receipts.pop(str(row), None)
    receipts.update({str(row): identity for row, identity in (current.get("verified_rows") or {}).items()})
    if active_rows is not None:
        receipts = {row: identity for row, identity in receipts.items() if int(row) in active_rows}
    return {**current, "verified_rows": receipts}


async def check_visual_rows(*, rows, cells, style, run_id, db_path, pdf_path,
                            sheet, model, usage, limits, output_dir, trace_label,
                            layouts=None, activity=None):
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
                + "\nOUTPUT PROFILE: " + json.dumps(asdict(style))
                + f"\nPREPARATION TIER: {identity.get('tier', 'unknown')}"
                + "\nRULE MAP (one entry per physical line, resolved from both cells):\n"
                + rule_map_text(rows[row])
                + "\nFORMATTER LAYOUT (its description of the source; context only, "
                "judge the actual HTML): " + _layout_text((layouts or {}).get(row))
                + "\n" + (_table_packets(rows[row], prepared)
                          or "CANONICAL CANDIDATE HTML:\n" + rows[row]
                          + "\nPREPARED OUTPUT HTML:\n" + prepared)
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
    deps.activity = activity
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
    ineligible: set[tuple[int, int]] = set()
    table_text = {
        (row, index): _plain(table.get_text(" ", strip=True))
        for row in {row for row, _ in reviewable}
        for index, table in enumerate(BeautifulSoup(rows[row], "html.parser").find_all("table"))
    }
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
        if issue:
            # Rejected evidence proves nothing about the table, whatever it
            # quotes: it stays unresolved and never triggers a re-copy.
            assessment.status = "unresolved"
            ineligible.add(key)
        elif assessment.content_defect.strip():
            # Wrong words or figures cannot be repaired by a re-copy that must
            # keep every word, nor by formatting. Report them once, together.
            also = f" Also reported: {assessment.reason}" if assessment.issue_kind != "source_content" else ""
            assessment.issue_kind = "source_content"
            assessment.status = "unresolved"
            issue = ("Source correction or human review required: "
                     + assessment.content_defect.strip() + "." + also)
        elif (assessment.issue_kind == "structure"
              and (not assessment.observed_text.strip()
                   or _plain(assessment.observed_text) not in table_text.get(key, ""))):
            # A structure claim that names no text in this table is not
            # evidence about this table; it must not trigger a re-copy.
            assessment.status = "unresolved"
            ineligible.add(key)
            issue = ("Checker structure finding did not quote text from this table, so it "
                     "was not acted on: " + assessment.reason)
        elif assessment.issue_kind in SOURCE_DEFECT_KINDS:
            # Neither a style patch nor a table re-copy repairs these.
            assessment.status = "unresolved"
            issue = "Source correction or human review required: " + assessment.reason
        elif assessment.status != "pass":
            issue = assessment.reason
        elif assessment.issue_kind == "structure":
            # A structural defect is never a pass, whatever the status says.
            assessment.status = "unresolved"
            issue = assessment.reason
        if issue:
            finding = f"Table {assessment.table}: {issue}"
            _add_error(errors, assessment.row, finding)
    for row, _ in reviewable - seen:
        _add_error(errors, row, "Visual assessment omitted a table.")
    dumped = decision.model_dump()
    for item in dumped["tables"]:
        if (item["row"], item["table"]) in ineligible:
            item["recopy_eligible"] = False
    return errors, {**dumped, "evidence": evidence}


async def review_and_correct_rows(
    *, rows: dict[int, str], cells, style, run_id: int, db_path: str,
    pdf_path: str, sheet: str, model, usage: RunUsage,
    formatter_run: Callable[[Any], Awaitable[Any]], output_dir: str,
    trace_label: str, on_phase: Callable[[str], None] | None = None,
    originals: dict[int, str] | None = None, layouts: dict[int, dict] | None = None,
    cell_padding: tuple[int, int] = (3, 3), activity: Any = None,
) -> tuple[dict[int, str], dict, dict[int, str]]:
    """Keep passed rows when a later correction or check cannot finish.

    ``originals`` is the HTML each row had when the pass started. A correction
    layout compiles onto it, so it fully replaces the earlier candidate.
    ``layouts`` (the accepted layout per row) is reviewer context only.
    """
    from notes.formatting_agent import _output_json, _partition_layout, _screen_patch
    from notes.table_layout import layout_manifest
    originals = originals if originals is not None else rows
    layouts = layouts if layouts is not None else {}

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
            output_dir=output_dir, trace_label=trace_label + suffix, layouts=layouts,
            activity=activity,
        )

    try:
        phase("Checking formatted notes against the source PDF…")
        errors, review = await check(rows, "_visual")
        # Structural findings go back to preparation (notes/table_recopy.py);
        # an appearance correction cannot repair a wrongly copied table.
        correctable = {item["row"] for item in review.get("tables", [])
                       if item["status"] == "needs_correction" and item["row"] in errors
                       and item.get("issue_kind", "style") == "style"}
        if not correctable:
            return errors, review, {}

        phase("Correcting table formatting from the visual findings…")
        failed_current = {row: rows[row] for row in correctable}
        try:
            correction = await formatter_run(
                "VISUAL REVIEW FAILED. Return a SheetLayout for only these rows. Each row's "
                "layout REPLACES its previous layout entirely, so describe every table again "
                "with the findings corrected against the source PDF. Text and geometry cannot "
                "change. Findings: "
                + json.dumps({row: errors[row] for row in correctable})
                + "\nPREVIOUS LAYOUTS: " + json.dumps({row: layouts.get(row) for row in correctable})
                + "\nLAYOUT COORDINATES: " + json.dumps({row: layout_manifest(originals[row])
                                                       for row in correctable}),
            )
            correction_error, screened, _ = _screen_patch(_output_json(correction.output), sheet, revised=True)
            if correction_error is not None:
                raise FormatPatchError(correction_error["error"])
            if any(note.get("row") not in failed_current for note in screened.patch.get("notes") or []
                   if isinstance(note, dict)):
                raise FormatPatchError("Visual correction targeted an unreviewed row")
            base = {row: originals[row] for row in correctable}
            safe, _, correction_errors = _partition_layout(base, screened.patch, cell_padding)
            for row, error in correction_errors.items():
                logger.warning("Visual correction rejected run=%s sheet=%s row=%s error=%s",
                               run_id, sheet, row, error)
            corrected_ok = {cell["row"] for cell in safe["cells"]}
            for note in screened.patch.get("notes") or []:
                if isinstance(note, dict) and note.get("row") in corrected_ok:
                    layouts[note["row"]] = note
            review["correction_patch"] = safe
            # Rows the correction did not cover keep their reviewed candidate.
            corrected_rows = apply_sheet_patch(base, safe).rows
            corrected = {row: corrected_rows[row] if row in corrected_ok else failed_current[row]
                         for row in correctable}
            phase("Rechecking corrected notes against the source PDF…")
            remaining, recheck = await check(corrected, "_visual_recheck")
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
            return errors, review, {row: html for row, html in corrected.items() if row not in errors}
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
