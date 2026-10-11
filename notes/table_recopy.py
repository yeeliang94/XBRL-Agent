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
    if not ids or not verify_format_only(rendered.html, cell["html"] or "").ok:
        raise RecopyRefused(f"{sheet} row {row} no longer matches its source exactly")
    return cell, rendered.block_ids, wrap


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
        raise RecopyRefused("tables continued across pages are not re-copied automatically")
    return block


async def recopy_tables(*, run_id: int, db_path: str, pdf_path: str, sheet: str,
                        findings: dict[int, list[tuple[int, str]]], model: Any,
                        model_name: str = "", on_phase: Callable[[str], None] | None = None,
                        _recapture: Optional[Callable] = None) -> dict:
    """Re-copy each flagged table once; return rebuilt rows and refusals.

    ``findings`` maps a note row to (table index, review reason) pairs.
    """
    from ingest.document_preparation import (
        PREPARATION_NAME, PreparationError, recapture_table, record_table_correction,
    )
    recapture = _recapture or recapture_table
    metadata_path = Path(pdf_path).parent / PREPARATION_NAME
    rebuilt: set[int] = set()
    errors: dict[int, str] = {}
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
                    homes = {(p["sheet"], p["row"]) for p in
                             srepo.placements_for_block(conn, generation["id"], block.block_id)}
                    if any(home_sheet != sheet for home_sheet, _ in homes):
                        raise RecopyRefused("the table is also used on another notes sheet")
                    revisions = {home_row: _source_cell(conn, run_id, generation, blocks, sheet, home_row)[0]
                                 ["content_revision"] for _, home_row in homes}
                data = json.loads(metadata_path.read_text(encoding="utf-8"))
                prepared = next((b for b in data["blocks"] if b["block_id"] == block.block_id), None)
                page = next((p for p in data["pages"] if prepared and p["page"] == prepared["page"]), None)
                if prepared is None or page is None or prepared["canonical_html"] != block.canonical_html:
                    raise RecopyRefused("the prepared document does not hold this table")
                if on_phase:
                    on_phase(f"Re-copying a table in note row {row} from the source PDF…")
                result = await recapture(
                    pdf_path, page_number=page["page"], page_html=page["html"],
                    block_index=prepared["locator"]["page_block"], finding=reason, model=model,
                    rotation=page.get("rotation") or 0)
                for key in tokens:
                    tokens[key] += int((result.get("usage") or {}).get(key) or 0)
                new_html = result["html"]
                rows = _commit(db_path, run_id=run_id, generation_id=generation["id"], sheet=sheet,
                               block_id=block.block_id, before=block.canonical_html, after=new_html,
                               revisions=revisions, finding=reason,
                               verification=result["verification"], model_name=model_name)
                done_blocks.add(block.block_id)
                rebuilt.update(rows)
                record = {"page": page["page"], "finding": reason, "rebuilt_rows": rows,
                          "created_at": datetime.now(timezone.utc).isoformat()}
                corrected.append({"block_id": block.block_id, "row": row, "table": table, **record})
                try:
                    record_table_correction(metadata_path, block_id=block.block_id,
                                            table_html=new_html, record=record)
                except Exception:  # noqa: BLE001 — the run's source is already corrected
                    logger.warning("Could not record table correction on disk run=%s block=%s",
                                   run_id, block.block_id, exc_info=True)
                    errors.setdefault(row, "Table re-copied for this run; a rerun may need it again.")
            except (RecopyRefused, PreparationError, ValueError) as exc:
                errors[row] = f"Table {table} could not be re-copied: {exc}"
    return {"rebuilt_rows": sorted(rebuilt), "errors": errors, "corrected": corrected, "usage": tokens}


def _commit(db_path: str, *, run_id: int, generation_id: int, sheet: str, block_id: str,
            before: str, after: str, revisions: dict[int, int], finding: str,
            verification: dict, model_name: str) -> list[int]:
    """Correct the block and rebuild every placed cell in one transaction."""
    now = datetime.now(timezone.utc).isoformat()
    with repo.db_session(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            generation = srepo.active_generation(conn, run_id)
            if generation is None or generation["id"] != generation_id:
                raise RecopyRefused("the source changed during the re-copy")
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
                digest = content_sha256(rendered.html)
                conn.execute(
                    "UPDATE notes_cells SET html=?, style_source='unstyled', updated_at=?, "
                    "current_html_sha256=?, source_rendered_sha256=?, "
                    "content_revision=COALESCE(content_revision,0)+1 "
                    "WHERE run_id=? AND sheet=? AND row=?",
                    (rendered.html, now, digest, rendered.source_rendered_sha256, run_id, sheet, row))
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
