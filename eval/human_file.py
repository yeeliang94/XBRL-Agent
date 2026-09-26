"""Read a human-filled mTool workbook against one run and store it.

A user attaches the mTool file a person filled for the same document as a
completed run. This module reads it with the run's own filing shape (standard,
level, exact template set) and stores the human's figures and notes so the
comparison can be recomputed from the run's current facts on every read.

Figures are read by address, the inverse of the mTool fill: every fillable
slot of the run's templates is resolved to its cell with
``mtool.template_map.resolve_filing_doc`` and the cell is read. A typed number
is the human's value. A formula cell is calculated by mTool, so the slot is
stored as ``calculated`` and left out of the comparison on both sides. Label
matching is not used: labels repeat within a sheet and silently merge
concepts (docs/human-mtool-file-comparison-plan.md, Step 1).

Notes are tied to a field by the taxonomy element ID the mTool note row
carries, joined to ``template_slots``. Prose is never matched by label. The
uploaded HTML is sanitised with the notes whitelist before it is stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from concept_model.parser import _derive_template_id
from mtool.column_detect import (
    ConflictingPeriodMarkersError,
    category_domain_rows,
    detect_column_map,
)
from mtool.exporter import _column_role
from mtool.offline_fill import (
    get_shared_strings,
    get_sheet_paths,
    inspect_footnotes,
    load_workbook_entries,
    read_footnote_rows,
    split_ref,
)
from mtool.template_map import index_workbook, resolve_filing_doc
from mtool.units import MONETARY, unit_class_for_label
from notes.html_sanitize import sanitize_notes_html
from notes.html_to_text import rendered_length
from notes_types import NOTES_REGISTRY, notes_template_path
from statement_types import VARIANTS, StatementType, template_path

UNIT_FACTORS: dict[str, float] = {
    "units": 1.0, "thousands": 1_000.0, "millions": 1_000_000.0,
}
COMPARABLE_RUN_STATUSES = frozenset({"completed", "completed_with_errors"})

# Numeric notes sheets whose values are split by a category axis. A concept
# without its own member is expanded over every member the taxonomy allows,
# but only when the workbook carries category columns.
_CATEGORY_AXIS_BY_SHEET = {
    "Notes-Issuedcapital": "ClassesOfShareCapitalAxis",
    "Notes-RelatedPartytran": "CategoriesOfRelatedPartiesAxis",
}


class HumanFileError(ValueError):
    """The file or run cannot be compared. The message is user-facing;
    ``status`` is the HTTP status the API returns for it."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class HumanFileRead:
    facts: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    not_compared: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def run_filing_shape(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """The run's standard, level, denomination and template sets.

    ``statements`` maps each compared template to its display name;
    ``notes_templates`` are the prose-notes templates the run extracted.
    Raises ``HumanFileError`` for a missing or unfinished run.
    """
    from db import repository as repo

    run = repo.fetch_run(conn, run_id)
    if run is None:
        raise HumanFileError("Run not found.", status=404)
    if run.status not in COMPARABLE_RUN_STATUSES:
        raise HumanFileError(
            "Only a completed run can be compared with a human file.", status=409)
    config = run.config or {}
    standard = str(config.get("filing_standard") or "mfrs").lower()
    level = str(config.get("filing_level") or "company").lower()

    statements: dict[str, str] = {}
    for statement in config.get("statements") or []:
        variant = (config.get("variants") or {}).get(statement)
        try:
            path = template_path(StatementType(statement), variant, level, standard)
        except (KeyError, ValueError):
            continue  # NotPrepared or unknown variant: nothing to compare
        statements[_derive_template_id(path)] = statement
    notes_templates: set[str] = set()
    notes_to_run = set(config.get("notes_to_run") or [])
    for template_type, entry in NOTES_REGISTRY.items():
        if template_type.value not in notes_to_run:
            continue
        try:
            template_id = _derive_template_id(
                notes_template_path(template_type, level, standard))
        except ValueError:
            continue
        notes_templates.add(template_id)
        if entry.is_numeric:
            statements[template_id] = entry.sheet_name
    return {
        "standard": standard,
        "level": level,
        "denomination": str(config.get("denomination") or "thousands").lower(),
        "statements": statements,
        "notes_templates": notes_templates,
    }


def _other_variant_templates(statement: str, level: str, standard: str,
                             exclude: str) -> dict[str, str]:
    """``{template_id: variant name}`` for the statement's other variants."""
    out: dict[str, str] = {}
    for (stmt, variant_name) in VARIANTS:
        if stmt.value != statement:
            continue
        try:
            path = template_path(stmt, variant_name, level, standard)
        except ValueError:
            continue
        template_id = _derive_template_id(path)
        if template_id != exclude:
            out[template_id] = variant_name
    return out


def _template_sheets(conn: sqlite3.Connection, template_ids) -> dict[str, set[str]]:
    ids = list(template_ids)
    if not ids:
        return {}
    out: dict[str, set[str]] = defaultdict(set)
    for template_id, sheet in conn.execute(
        "SELECT DISTINCT template_id, render_sheet FROM concept_nodes "
        f"WHERE is_current = 1 AND template_id IN ({','.join('?' * len(ids))})",
        ids,
    ):
        out[template_id].add(sheet)
    return out


def _slot_writes(conn, template_ids, level: str, standard: str,
                 cells_by_sheet: dict) -> list[dict[str, Any]]:
    """One addressing write per fillable slot, shaped like the exporter's."""
    ids = list(template_ids)
    if not ids:
        return []
    rows = conn.execute(
        """
        SELECT n.concept_uuid, n.kind, n.render_sheet, n.canonical_label,
               n.template_id, t.period, t.entity_scope,
               t.target_sheet, t.target_row, t.target_col,
               sa.primary_concept, sa.dimensions_json,
               sa.taxonomy_version, sa.address_version,
               tc.namespace_uri, tc.local_name, tc.concept_role,
               tc.data_type, tc.period_type
        FROM concept_nodes n
        JOIN concept_targets t USING(concept_uuid)
        LEFT JOIN concept_semantic_addresses sa USING(concept_uuid)
        LEFT JOIN taxonomy_concepts tc ON tc.source_element_id = sa.primary_concept
        WHERE n.is_current = 1 AND n.kind IN ('LEAF', 'MATRIX_CELL')
        """ + f"AND n.template_id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    writes: list[dict[str, Any]] = []
    for (uuid, kind, sheet, label, template_id, period, scope, t_sheet, t_row,
         t_col, primary, dims_json, tax_version, addr_version, namespace,
         local_name, role_name, data_type, period_type) in rows:
        if (data_type or "").lower().endswith("textblockitemtype"):
            continue
        role = _column_role(period, scope, level)
        if role is None:
            continue
        semantic = None
        if primary:
            semantic = {
                "primary_concept": primary,
                "dimensions": json.loads(dims_json or "{}"),
                "taxonomy_version": tax_version,
                "address_version": addr_version,
                "namespace_uri": namespace,
                "local_name": local_name,
                "concept_role": role_name,
                "data_type": data_type,
                "period_type": period_type,
            }
        write = {
            "sheet": sheet, "label": label, "column_role": role, "value": 0,
            "concept_uuid": uuid, "period": period, "entity_scope": scope,
            "dimension_key": "", "kind": kind, "template_id": template_id,
            "semantic_address": semantic,
            "unit_class": unit_class_for_label(label, standard),
        }
        if t_sheet and t_row and t_col:
            write["target_hint"] = {"sheet": t_sheet, "row": t_row, "col": t_col}
        axis_suffix = _CATEGORY_AXIS_BY_SHEET.get(sheet)
        if (axis_suffix and semantic and not semantic["dimensions"]
                and category_domain_rows(cells_by_sheet.get(sheet, {}))):
            from concept_model.dimensions import dimension_key, numeric_category_catalog
            for axis, members in numeric_category_catalog(standard).items():
                if not axis.endswith(axis_suffix):
                    continue
                for member in members:
                    dims = {axis: member}
                    writes.append({
                        **write, "dimension_key": dimension_key(dims),
                        "semantic_address": {**semantic, "dimensions": dims},
                    })
        else:
            writes.append(write)
    return writes


def _row_label(row_cells: dict, label_col: str | None) -> str:
    if label_col:
        cell = row_cells.get(label_col)
        if cell and cell[0] == "S" and cell[1].strip():
            return cell[1].strip()
        return ""
    return next((text.strip() for kind, text in row_cells.values()
                 if kind == "S" and text.strip() and ".xsd#" not in text), "")


def _typed_rows(cells: dict, label_col: str | None) -> dict[int, dict]:
    """Rows with a label and at least one typed number: ``{row: {label, values}}``."""
    out: dict[int, dict] = {}
    for row_num, row_cells in cells.items():
        values = {}
        for col, (kind, text) in row_cells.items():
            if kind != "N":
                continue
            try:
                values[col] = float(text)
            except (TypeError, ValueError):
                continue
        if not values:
            continue
        label = _row_label(row_cells, label_col)
        if label:
            out[row_num] = {"label": label, "values": values}
    return out


def _element_id(row_text: dict) -> str | None:
    """Taxonomy element ID from an mTool note row: the ``.xsd#`` reference
    without its file prefix or ``@role`` suffix."""
    for text in row_text.values():
        if ".xsd#" in text:
            return text.split("#", 1)[1].split("@", 1)[0].strip() or None
    return None


# Excel Xstring escapes: _xHHHH_ is one character; _x005F_ before another
# escape keeps that escape as literal text.
_XSTRING_ESCAPE = re.compile(r"_x005[fF]_(_x[0-9a-fA-F]{4}_)|_x([0-9a-fA-F]{4})_")
_BODY = re.compile(r"<body\b[^>]*>(.*)</body>", re.DOTALL | re.IGNORECASE)


def _note_fragment(payload: str) -> str:
    """The note HTML inside mTool's XHTML text-block shell.

    Excel stores the shell's line breaks as ``_x000D_`` tokens. They are
    decoded, and only the body is kept so the shell's head never shows.
    """
    text = _XSTRING_ESCAPE.sub(
        lambda m: m.group(1) or chr(int(m.group(2), 16)), payload)
    body = _BODY.search(text)
    return body.group(1) if body else text


def _read_notes(conn, data, standard: str, level: str,
                notes_templates: set[str]) -> tuple[list, list, dict]:
    sheet_paths = get_sheet_paths(data)
    payloads = read_footnote_rows(data, sheet_paths, get_shared_strings(data))
    targets = inspect_footnotes(data)["targets"]
    family = f"{standard}-{level}-%"
    notes: list[dict] = []
    unmatched: list[dict] = []
    outside: dict[str, int] = defaultdict(int)
    for target in targets:
        payload = payloads.get(target["key"]) or {}
        if not payload.get("payload_populated"):
            continue
        element = _element_id(target["row_text"])
        slots = conn.execute(
            "SELECT DISTINCT canonical_target_id, template_id FROM template_slots "
            "WHERE taxonomy_element_id = ? AND template_id LIKE ? "
            "AND canonical_target_id IS NOT NULL",
            (element, family),
        ).fetchall() if element else []
        label = _row_label(
            {c: ("S", t) for c, t in target["row_text"].items()}, None)
        if len(slots) != 1:
            unmatched.append({
                "kind": "note", "sheet": target["sheet"], "row": target["row"],
                "label": label,
                "reason": "ambiguous" if slots else "unmatched",
            })
            continue
        concept_uuid, template_id = slots[0]
        if template_id not in notes_templates:
            outside[template_id] += 1
            continue
        # Uploaded prose is untrusted: keep only the notes tag whitelist.
        html, _ = sanitize_notes_html(_note_fragment(payload["payload_text"]))
        if rendered_length(html) == 0:
            continue
        notes.append({"concept_uuid": concept_uuid, "note_key": target["key"],
                      "html": html})
    return notes, unmatched, dict(outside)


def _magnitude_warning(conn, run_id: int, facts: list[dict]) -> str | None:
    """Warn when human values sit ~1,000x from the run's on shared slots."""
    ai = {
        (r[0], r[1], r[2], r[3] or ""): r[4]
        for r in conn.execute(
            "SELECT concept_uuid, period, entity_scope, dimension_key, value "
            "FROM run_concept_facts WHERE run_id = ? AND value IS NOT NULL",
            (run_id,),
        )
    }
    ratios = []
    for f in facts:
        if f.get("unit_class") != MONETARY or not f["value"]:
            continue
        other = ai.get((f["concept_uuid"], f["period"], f["entity_scope"],
                        f["dimension_key"]))
        if other:
            ratios.append(abs(f["value"]) / abs(other))
    if len(ratios) < 3:
        return None
    ratio = statistics.median(ratios)
    if 300 <= ratio <= 3000:
        return ("The human figures look about 1,000 times larger than the "
                "run's. Check the unit you chose for the file.")
    if 1 / 3000 <= ratio <= 1 / 300:
        return ("The human figures look about 1,000 times smaller than the "
                "run's. Check the unit you chose for the file.")
    return None


def read_human_file(conn: sqlite3.Connection, run_id: int, path: str | Path,
                    unit: str) -> HumanFileRead:
    """Read ``path`` as the human's filing for ``run_id``. Nothing is stored."""
    if unit not in UNIT_FACTORS:
        raise HumanFileError("Unit must be units, thousands or millions.")
    shape = run_filing_shape(conn, run_id)
    standard, level = shape["standard"], shape["level"]
    scale = UNIT_FACTORS[unit] / UNIT_FACTORS.get(shape["denomination"], 1_000.0)

    try:
        _, data, _ = load_workbook_entries(str(path))
    except Exception as exc:  # zipfile/XML errors from a non-mTool upload
        raise HumanFileError(
            "The file could not be read as an mTool workbook.") from exc
    workbook_index = index_workbook(data)
    _, cells_by_sheet = workbook_index

    statements = shape["statements"]
    writes = _slot_writes(conn, statements, level, standard, cells_by_sheet)
    unit_by_concept = {
        write["concept_uuid"]: (write["unit_class"], write["label"])
        for write in writes
    }
    sheets: dict[str, dict] = {}
    for write in writes:
        sheets.setdefault(write["sheet"], {"label_column": None, "columns": {}})[
            "columns"].setdefault(write["column_role"], None)
    doc = {"meta": {}, "sheets": sheets, "writes": writes, "strict": True}
    try:
        detected = detect_column_map(str(path), doc, data=data,
                                     cells_by_sheet=cells_by_sheet)
    except ConflictingPeriodMarkersError as exc:
        raise HumanFileError(
            "The file's year columns could not be told apart.") from exc
    column_map = {s: {"label_column": v.get("label_column"),
                      "columns": v.get("columns") or {}}
                  for s, v in detected.items()}
    ready, coverage = resolve_filing_doc(
        str(path), doc, data=data, column_map=column_map,
        workbook_index=workbook_index)

    result = HumanFileRead()
    claimed: dict[str, set[tuple[int, str]]] = defaultdict(set)
    by_template: dict[str, list[dict]] = defaultdict(list)
    for write in ready["writes"]:
        if not write.get("cell"):
            continue  # label-only fallback: never read by guess
        col, row = split_ref(write["cell"])
        claimed[write["sheet"]].add((row, col))
        # Blank cells are kept (value None): they separate "the human left
        # this field empty" from "the human's file has no such field".
        cell = cells_by_sheet.get(write["sheet"], {}).get(row, {}).get(col)
        unit_class, label = unit_by_concept[write["concept_uuid"]]
        if scale != 1 and unit_class is None and cell and cell[0] == "N":
            raise HumanFileError(
                f"The unit of {label!r} on {write['sheet']} is unknown; "
                "the file cannot be converted safely."
            )
        fact = {
            "concept_uuid": write["concept_uuid"], "period": write["period"],
            "entity_scope": write["entity_scope"],
            "dimension_key": write.get("dimension_key") or "",
            "value": None, "calculated": bool(cell and cell[0] == "F"),
            "unit_class": unit_class,
        }
        if cell and cell[0] == "N":
            try:
                fact["value"] = float(cell[1]) * (scale if unit_class == MONETARY else 1)
            except (TypeError, ValueError):
                pass
        by_template[write["template_id"]].append(fact)

    sheets_by_template = _template_sheets(conn, statements)
    typed_by_sheet = {
        sheet: _typed_rows(cells_by_sheet.get(sheet, {}),
                           (detected.get(sheet) or {}).get("label_column"))
        for sheet in {s for ss in sheets_by_template.values() for s in ss}
    }
    for template_id, name in statements.items():
        facts = by_template.get(template_id, [])
        unmatched = []
        for sheet in sorted(sheets_by_template.get(template_id, ())):
            for row, info in sorted(typed_by_sheet.get(sheet, {}).items()):
                loose = {c: v for c, v in info["values"].items()
                         if (row, c) not in claimed[sheet]}
                if loose:
                    unmatched.append({"kind": "figure", "sheet": sheet, "row": row,
                                      "label": info["label"], "values": loose})
        if any(f["value"] is not None for f in facts):
            result.facts.extend(facts)
            result.unmatched.extend(unmatched)
            continue
        others = _other_variant_templates(name, level, standard, template_id)
        filled_variant = next((
            variant for other_id, variant in others.items()
            if any(_typed_rows(cells_by_sheet.get(sheet, {}), None)
                   for sheet in _template_sheets(conn, [other_id]).get(other_id, ()))
        ), None)
        result.not_compared.append({
            "template_id": template_id, "statement": name,
            "reason": "different_variant" if filled_variant else "not_in_file",
            "human_variant": filled_variant,
        })

    notes, note_unmatched, outside = _read_notes(
        conn, data, standard, level, shape["notes_templates"])
    result.notes = notes
    result.unmatched.extend(note_unmatched)
    for template_id, count in outside.items():
        result.not_compared.append({
            "template_id": template_id, "statement": template_id,
            "reason": "not_in_run", "notes": count,
        })

    typed = sum(1 for f in result.facts if f["value"] is not None)
    calculated = sum(1 for f in result.facts if f["calculated"])
    if typed == 0 and not result.notes:
        variants = [f"{n['statement']} ({n['human_variant']})"
                    for n in result.not_compared if n.get("human_variant")]
        if variants:
            raise HumanFileError(
                "The human file uses a different statement layout: "
                + ", ".join(variants) + ". Nothing else could be compared.")
        raise HumanFileError(
            "No figures or notes in this file match the run's templates. "
            "Check that it is the mTool file for the same filing.")
    result.summary = {
        "typed_values": typed,
        "calculated_slots": calculated,
        "unresolved_slots": coverage.get("unmapped", 0) + coverage.get("ambiguous", 0),
        "unmatched_rows": len(result.unmatched),
        "notes": len(result.notes),
        "unit_scale": scale,
        "magnitude_warning": _magnitude_warning(conn, run_id, result.facts),
    }
    return result


def store_human_file(conn: sqlite3.Connection, run_id: int, read: HumanFileRead,
                     *, filename: str, sha256: str, unit: str,
                     uploaded_by: str | None) -> None:
    """Replace the run's human file with ``read`` in one transaction."""
    with conn:
        delete_rows(conn, run_id)
        conn.execute(
            "INSERT INTO human_files(run_id, filename, sha256, unit, uploaded_by, "
            "uploaded_at, summary_json, unmatched_json, not_compared_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, filename, sha256, unit, uploaded_by, _now(),
             json.dumps(read.summary), json.dumps(read.unmatched),
             json.dumps(read.not_compared)),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO human_file_facts(run_id, concept_uuid, period, "
            "entity_scope, dimension_key, value, calculated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(run_id, f["concept_uuid"], f["period"], f["entity_scope"],
              f["dimension_key"], f["value"], int(f["calculated"]))
             for f in read.facts],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO human_file_notes(run_id, concept_uuid, "
            "note_key, html) VALUES (?, ?, ?, ?)",
            [(run_id, n["concept_uuid"], n["note_key"], n["html"])
             for n in read.notes],
        )


def delete_rows(conn: sqlite3.Connection, run_id: int) -> bool:
    """Remove the run's human file. The caller owns the transaction."""
    conn.execute("DELETE FROM human_file_notes WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM human_file_facts WHERE run_id = ?", (run_id,))
    return conn.execute(
        "DELETE FROM human_files WHERE run_id = ?", (run_id,)).rowcount > 0


def ingest_human_file(conn: sqlite3.Connection, run_id: int, path: str | Path,
                      *, filename: str, unit: str,
                      uploaded_by: str | None) -> dict[str, Any]:
    """Read and store a human file for a run; return the stored record."""
    read = read_human_file(conn, run_id, path, unit)
    sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    store_human_file(conn, run_id, read, filename=filename, sha256=sha256,
                     unit=unit, uploaded_by=uploaded_by)
    return load_human_file(conn, run_id)


def load_human_file(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT filename, sha256, unit, uploaded_by, uploaded_at, summary_json, "
        "unmatched_json, not_compared_json FROM human_files WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "run_id": run_id, "filename": row[0], "sha256": row[1], "unit": row[2],
        "uploaded_by": row[3], "uploaded_at": row[4],
        "summary": json.loads(row[5] or "{}"),
        "unmatched": json.loads(row[6] or "[]"),
        "not_compared": json.loads(row[7] or "[]"),
    }
