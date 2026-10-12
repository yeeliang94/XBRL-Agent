"""Send a wrongly copied source table back to preparation, then rebuild.

A structural visual-review finding (the copied table differs from the PDF in
cell types, rows, columns or heading placement) cannot be fixed by the
formatter, which only changes appearance. Here the table is re-copied with
preparation's own capture and verification prompts, the source block itself
is corrected, and every note built from it is re-rendered from its blocks. The
notes therefore still match their source exactly; nothing needs a separate
receipt. Anything ineligible or unconfirmed stays flagged, unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from bs4 import BeautifulSoup

from db import repository as repo
from notes import source_repository as srepo
from notes.format_verify import verify_format_only
from notes.lineage import content_sha256
from notes.source_render import BlockSelectionError, render_blocks, uses_subnote_sections
from notes.source_write import load_blocks

logger = logging.getLogger(__name__)


class RecopyRefused(ValueError):
    """The table is not eligible for an automatic re-copy."""


def _source_cell(conn, run_id: int, generation, blocks, sheet: str, row: int):
    """A cell that still equals the render of its placed blocks, or a refusal."""
    cell = conn.execute("SELECT * FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
                        (run_id, sheet, row)).fetchone()
    if cell is None:
        raise RecopyRefused(f"{sheet} row {row} no longer exists")
    if cell["style_source"] == "human" or cell["content_origin"] == "human_modified":
        raise RecopyRefused(f"{sheet} row {row} was edited after it was copied from the source")
    if cell["source_generation_id"] != generation["id"]:
        raise RecopyRefused(f"{sheet} row {row} is not built from the active source")
    ids = [p["block_id"] for p in srepo.active_placements(conn, generation["id"])
           if (p["sheet"], p["row"]) == (sheet, row)]
    wrap = uses_subnote_sections(generation["input_kind"], cell["source_render_version"])
    try:
        rendered = render_blocks(blocks, ids, wrap_subnotes=wrap)
    except BlockSelectionError as exc:
        raise RecopyRefused(str(exc)) from exc
    if not ids:
        raise RecopyRefused(f"{sheet} row {row} has no source placement")
    if not verify_format_only(rendered.html, cell["html"] or "").ok:
        # Page-furniture cleanup is the one accepted difference; it removes
        # short text lines, never tables, so it can be replayed after a re-copy.
        from notes.cleanup_repository import receipt_matches
        if not receipt_matches(conn, run_id, sheet, row, generation["id"], rendered.html, cell["html"] or ""):
            raise RecopyRefused(f"{sheet} row {row} no longer matches its source exactly")
    return cell, rendered.block_ids, wrap


def _continued_refusal(blocks, block) -> RecopyRefused:
    """Name the pages and any recorded preparation doubt, so the row says
    exactly which source correction a person must make."""
    group = [b for b in blocks
             if b.block_id == block.block_id
             or (block.table_group_id and b.table_group_id == block.table_group_id)
             or b.continues_block_id == block.block_id
             or block.continues_block_id == b.block_id]
    pages = sorted({b.page for b in group if b.page is not None})
    doubts = sorted({str((b.locator or {}).get("uncertainty_reason") or "").strip()
                     for b in group} - {""})
    where = (f"pages {pages[0]}-{pages[-1]}" if len(pages) > 1
             else f"page {pages[0]}" if pages else "several pages")
    message = (f"needs source correction: the table continues across {where}, "
               "and continued tables are not re-copied automatically")
    if doubts:
        message += "; preparation already recorded: " + "; ".join(doubts)
    return RecopyRefused(message)


def _table_block(blocks, ordered_ids: list[str], html: str, table: int):
    by_id = {b.block_id: b for b in blocks}
    tables = [by_id[bid] for bid in ordered_ids if by_id[bid].block_kind == "table"]
    rendered_tables = BeautifulSoup(html, "html.parser").find_all("table")
    if any(len(BeautifulSoup(b.canonical_html, "html.parser").find_all("table")) != 1 for b in tables) \
            or len(tables) != len(rendered_tables) or table >= len(tables):
        raise RecopyRefused("the table cannot be matched to exactly one source table")
    block = tables[table]
    continued = any(b.continues_block_id == block.block_id for b in blocks)
    if block.table_group_id or block.continues_block_id or continued:
        raise _continued_refusal(blocks, block)
    return block


def _block_homes(conn, run_id: int, generation, blocks, block, sheet: str) -> dict[int, int]:
    """Rows built from this block, with their content revisions, or a refusal."""
    continued = any(b.continues_block_id == block.block_id for b in blocks)
    if block.table_group_id or block.continues_block_id or continued:
        raise _continued_refusal(blocks, block)
    homes = {(p["sheet"], p["row"]) for p in
             srepo.placements_for_block(conn, generation["id"], block.block_id)}
    if any(home_sheet != sheet for home_sheet, _ in homes):
        raise RecopyRefused("the table is also used on another notes sheet")
    return {home_row: _source_cell(conn, run_id, generation, blocks, sheet, home_row)[0]["content_revision"]
            for _, home_row in homes}


async def recopy_tables(*, run_id: int, db_path: str, pdf_path: str, sheet: str,
                        findings: dict[int, list[tuple[int, str]]], model: Any,
                        model_name: str = "", on_phase: Callable[[str], None] | None = None,
                        _recapture: Optional[Callable] = None) -> dict:
    """Re-copy each flagged table once; return rebuilt rows and refusals.

    ``findings`` maps a note row to (table index, review reason) pairs. One
    page re-copy may also correct another table on that page with the same
    defect; that table is saved only if it passes the same eligibility rules.
    """
    from ingest.document_preparation import (
        PREPARATION_NAME, PreparationError, recapture_table, record_table_correction,
    )
    recapture = _recapture or recapture_table
    metadata_path = Path(pdf_path).parent / PREPARATION_NAME
    rebuilt: set[int] = set()
    errors: dict[int, str] = {}
    # The run's note is corrected, but a rerun of the upload may not be.
    warnings: dict[int, str] = {}
    corrected: list[dict] = []
    done_blocks: set[str] = set()
    tokens = {"prompt_tokens": 0, "completion_tokens": 0}
    for row, items in sorted(findings.items()):
        for table, reason in items:
            try:
                with repo.db_session(db_path) as conn:
                    generation = srepo.active_generation(conn, run_id)
                    if generation is None or generation["input_kind"] != "prepared_document":
                        raise RecopyRefused("only tables copied from a prepared PDF can be re-copied")
                    blocks = load_blocks(conn, generation["id"])
                    cell, ordered, _wrap = _source_cell(conn, run_id, generation, blocks, sheet, row)
                    block = _table_block(blocks, ordered, cell["html"], table)
                    if block.block_id in done_blocks:
                        continue
                    revisions = _block_homes(conn, run_id, generation, blocks, block, sheet)
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                prepared = next((b for b in data["blocks"] if b["block_id"] == block.block_id), None)
                page = next((p for p in data["pages"] if prepared and p["page"] == prepared["page"]), None)
                if prepared is None or page is None or prepared["canonical_html"] != block.canonical_html:
                    raise RecopyRefused("the prepared document does not hold this table")
                if on_phase:
                    on_phase(f"Re-copying a table in note row {row} from the source PDF…")
                usage: dict = {}
                try:
                    result = await recapture(
                        pdf_path, page_number=page["page"], page_html=page["html"],
                        block_index=prepared["locator"]["page_block"], finding=reason, model=model,
                        rotation=page.get("rotation") or 0, usage_out=usage)
                finally:
                    for key in tokens:
                        tokens[key] += int(usage.get(key) or 0)
                on_page = {b["locator"]["page_block"]: b for b in data["blocks"] if b["page"] == page["page"]}
                changes = {block.block_id: (block.canonical_html, result["html"])}
                with repo.db_session(db_path) as conn:
                    by_id = {b.block_id: b for b in blocks}
                    for index, html in (result.get("tables") or {}).items():
                        other = on_page.get(int(index))
                        if other is None or other["block_id"] in changes or other["block_id"] in done_blocks:
                            continue
                        source = by_id.get(other["block_id"])
                        try:
                            if source is None or source.canonical_html != other["canonical_html"]:
                                raise RecopyRefused("not the saved source table")
                            revisions.update(_block_homes(conn, run_id, generation, blocks, source, sheet))
                        except RecopyRefused as exc:
                            logger.info("Second table on page %s not corrected: %s", page["page"], exc)
                            continue
                        changes[source.block_id] = (source.canonical_html, html)
                rows = _commit(db_path, run_id=run_id, generation_id=generation["id"], sheet=sheet,
                               changes=changes, revisions=revisions, finding=reason,
                               verification=result["verification"], model_name=model_name)
                done_blocks.update(changes)
                rebuilt.update(rows)
                for block_id, (_before, after) in changes.items():
                    record = {"page": page["page"], "finding": reason, "rebuilt_rows": rows,
                              "created_at": datetime.now(timezone.utc).isoformat()}
                    corrected.append({"block_id": block_id, "row": row, "table": table, **record})
                    try:
                        record_table_correction(metadata_path, block_id=block_id, table_html=after, record=record)
                    except Exception:  # noqa: BLE001 — the run's source is already corrected
                        logger.warning("Could not record table correction on disk run=%s block=%s",
                                       run_id, block_id, exc_info=True)
                        warnings.setdefault(row, "Table re-copied for this run only; rerunning the "
                                                 "upload may bring back the old table.")
            except (RecopyRefused, PreparationError, ValueError) as exc:
                message = f"Table {table} could not be re-copied: {exc}"
                errors[row] = f"{errors[row]}; {message}" if row in errors else message
            except Exception as exc:  # noqa: BLE001 — a provider failure stays on this row
                # Earlier tables may already be committed; escaping here would
                # discard their rebuilt rows and the formatter's saved outcome.
                logger.warning("Table re-copy failed run=%s sheet=%s row=%s table=%s",
                               run_id, sheet, row, table, exc_info=True)
                message = f"Table {table} could not be re-copied: {type(exc).__name__}: {exc}"
                errors[row] = f"{errors[row]}; {message}" if row in errors else message
    return {"rebuilt_rows": sorted(rebuilt), "errors": errors, "warnings": warnings, "corrected": corrected, "usage": tokens}


def _commit(db_path: str, *, run_id: int, generation_id: int, sheet: str,
            changes: dict[str, tuple[str, str]], revisions: dict[int, int], finding: str,
            verification: dict, model_name: str) -> list[int]:
    """Correct the blocks and rebuild every placed cell in one transaction."""
    now = datetime.now(timezone.utc).isoformat()
    with repo.db_session(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            generation = srepo.active_generation(conn, run_id)
            if generation is None or generation["id"] != generation_id:
                raise RecopyRefused("the source changed during the re-copy")
            for block_id, (before, after) in changes.items():
                current = conn.execute("SELECT canonical_html FROM notes_source_blocks "
                                       "WHERE generation_id=? AND block_id=?", (generation_id, block_id)).fetchone()
                if current is None or current["canonical_html"] != before:
                    raise RecopyRefused("the source table changed during the re-copy")
                conn.execute("UPDATE notes_source_blocks SET canonical_html=?, content_sha256=? "
                             "WHERE generation_id=? AND block_id=?",
                             (after, hashlib.sha256(after.encode("utf-8")).hexdigest(), generation_id, block_id))
            blocks = load_blocks(conn, generation_id)
            for row, revision in sorted(revisions.items()):
                cell = conn.execute("SELECT * FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
                                    (run_id, sheet, row)).fetchone()
                if cell is None or cell["content_revision"] != revision or cell["style_source"] == "human":
                    raise RecopyRefused(f"{sheet} row {row} changed during the re-copy")
                ids = [p["block_id"] for p in srepo.active_placements(conn, generation_id)
                       if (p["sheet"], p["row"]) == (sheet, row)]
                rendered = render_blocks(blocks, ids, wrap_subnotes=uses_subnote_sections(
                    generation["input_kind"], cell["source_render_version"]))
                # Unchanged revision proves the cell still equals its old render.
                if not rendered.usable or rendered.oversized:
                    raise RecopyRefused(f"{sheet} row {row} cannot be rebuilt")
                html = _replay_cleanup(conn, run_id=run_id, generation_id=generation_id, cell=cell,
                                       rendered=rendered.html)
                conn.execute(
                    "UPDATE notes_cells SET html=?, style_source='unstyled', updated_at=?, "
                    "current_html_sha256=?, source_rendered_sha256=?, "
                    "content_revision=COALESCE(content_revision,0)+1 "
                    "WHERE run_id=? AND sheet=? AND row=?",
                    (html, now, content_sha256(html), rendered.source_rendered_sha256, run_id, sheet, row))
            for block_id, (before, after) in changes.items():
                conn.execute(
                    "INSERT INTO notes_source_block_revisions(run_id, generation_id, block_id, before_html, "
                    "after_html, finding, verification_json, rebuilt_cells_json, model, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, generation_id, block_id, before, after, finding, json.dumps(verification),
                     json.dumps([{"sheet": sheet, "row": row} for row in sorted(revisions)]), model_name, now))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return sorted(revisions)


def _replay_cleanup(conn, *, run_id: int, generation_id: int, cell, rendered: str) -> str:
    """Re-apply a cell's recorded page-furniture removals to its new render.

    Removals address short text blocks by position and never tables, so a
    table re-copy leaves them valid. The receipt is rewritten onto the new
    render so integrity keeps replaying it exactly.
    """
    from notes.cleanup_patch import Patch, apply_patch
    from notes.cleanup_repository import _receipt_steps, source_evidence

    receipt = conn.execute(
        "SELECT * FROM notes_cleanup_receipts WHERE run_id=? AND sheet=? AND row=? AND generation_id IS ?",
        (run_id, cell["sheet"], cell["row"], generation_id)).fetchone()
    if receipt is None:
        return rendered
    cid = f"{cell['sheet']}:{cell['row']}"
    evidence = source_evidence(conn, run_id, {**dict(cell), "source_generation_id": generation_id})
    html, history = rendered, []
    for step in _receipt_steps(receipt):
        patch = Patch.model_validate(step["patch"])
        after = apply_patch({cid: {**evidence, "html": html}}, patch, set(step["viewed_pages"]))[cid]["html"]
        history.append({**step, "before_html": html, "after_html": after})
        html = after
    conn.execute("UPDATE notes_cleanup_receipts SET before_html=?, after_html=?, patch_json=? WHERE id=?",
                 (rendered, html, json.dumps(history), receipt["id"]))
    return html
