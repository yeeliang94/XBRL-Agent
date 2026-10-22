"""Report values use registered canonical targets; narrative HTML stays in notes_cells."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
import sqlite3

from db import repository as runs
from notes.html_sanitize import sanitize_notes_html
from reports.registry import REPORTS, fields, report_for_sheet, template_path
from concept_model.parser import derive_template_id


class StaleReportEdit(ValueError):
    pass


def family(conn, run_id):
    run = runs.fetch_run(conn, run_id)
    if run is None:
        raise LookupError("Run not found")
    config = run.config or {}
    return config.get("filing_standard", "mfrs"), config.get("filing_level", "company"), run


def report_view(conn, run_id: int, report: str) -> dict:
    standard, level, _ = family(conn, run_id)
    template_id = derive_template_id(template_path(report, standard, level))
    conn.row_factory = sqlite3.Row
    stored = {r["concept_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM report_fields WHERE run_id=? AND report_type=?", (run_id, report))}
    result = []
    for f in fields(report, standard):
        item = {**asdict(f), "value": None, "value_status": "unresolved", "evidence": "", "source_pages": [], "revision": 0}
        if f.concept in stored:
            saved = stored[f.concept]
            item.update({k: saved[k] for k in ("value", "value_status", "evidence", "revision")})
            item["source_pages"] = json.loads(saved["source_pages"])
            target = conn.execute(
                "SELECT canonical_target_id FROM template_slots WHERE template_id=? AND taxonomy_element_id=? AND validation_status='writable'",
                (template_id, f.concept)).fetchall()
            if len(target) != 1 or target[0][0] != saved["target_id"]:
                item["value_status"] = "unresolved"
                item["value"] = None
                item["evidence"] = "The report target changed. Re-extract or review this field."
            elif f.kind == "html":
                row = conn.execute("SELECT html FROM notes_cells WHERE run_id=? AND sheet=? AND row=? AND concept_uuid=? AND invalid_target=0",
                                   (run_id, REPORTS[report][2], f.row, saved["target_id"])).fetchone()
                item["value"] = row[0] if row else None
        result.append(item)
    return {"type": report, "label": REPORTS[report][0], "sheet": REPORTS[report][2], "fields": result}


def bump_html_revision(conn, run_id: int, sheet: str, row: int) -> None:
    """Notes-layer writes (formatting, cleanup, revert) change saved report text.

    Bump the report revision so a reviewer holding the earlier text gets a
    stale-edit refusal instead of silently overwriting the newer HTML.
    """
    report = report_for_sheet(sheet)
    if report is None:
        return
    standard, _, _ = family(conn, run_id)
    concepts = [f.concept for f in fields(report, standard) if f.kind == "html" and f.row == row]
    if concepts:
        conn.execute(
            f"UPDATE report_fields SET revision=revision+1 WHERE run_id=? AND report_type=? "
            f"AND concept_id IN ({','.join('?' * len(concepts))})", (run_id, report, *concepts))


def save_field(conn, run_id: int, report: str, concept: str, value: str | None,
               status: str, evidence: str, pages: list[int], *, expected_revision: int | None = None) -> int:
    standard, level, _ = family(conn, run_id)
    f = next((f for f in fields(report, standard) if f.concept == concept), None)
    if f is None:
        raise ValueError("Unknown report field")
    if status not in {"observed", "not_disclosed", "unresolved", "user_override", "cleared"}:
        raise ValueError("Unknown report value status")
    if any(type(p) is not int or p < 1 for p in pages):
        raise ValueError("Source pages must be positive PDF page numbers")
    if status in {"not_disclosed", "unresolved", "cleared"}:
        value = None
    elif not isinstance(value, str) or not value.strip():
        raise ValueError("A disclosed field must have a value")
    if value is not None:
        if f.kind == "choice" and value not in f.choices:
            raise ValueError("Choose a value permitted by the SSM taxonomy")
        if f.kind == "date":
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("Dates must use YYYY-MM-DD")
        if f.kind == "html":
            value, _ = sanitize_notes_html(value)
            from notes.html_to_text import html_to_excel_text
            if not html_to_excel_text(value).strip():
                raise ValueError("Report narrative must contain text")
        elif len(value) > 4096:
            raise ValueError("Report field is too long")
    template_id = derive_template_id(template_path(report, standard, level))
    targets = conn.execute(
        "SELECT canonical_target_id FROM template_slots WHERE template_id=? AND taxonomy_element_id=? AND slot_role='INPUT' AND validation_status='writable' AND value_kind=?",
        (template_id, concept, f.kind)).fetchall()
    if len(targets) != 1:
        raise ValueError("Report destination is not registered as writable")
    target_id = targets[0][0]
    old = conn.execute("SELECT revision FROM report_fields WHERE run_id=? AND report_type=? AND concept_id=?",
                       (run_id, report, concept)).fetchone()
    revision = old[0] if old else 0
    if expected_revision is not None and expected_revision != revision:
        raise StaleReportEdit("This report changed. Reload it before saving.")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO report_fields(run_id,report_type,concept_id,target_id,value_kind,value,value_status,evidence,source_pages,revision,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,report_type,concept_id) DO UPDATE SET
        target_id=excluded.target_id,value_kind=excluded.value_kind,value=excluded.value,value_status=excluded.value_status,
        evidence=excluded.evidence,source_pages=excluded.source_pages,revision=excluded.revision,updated_at=excluded.updated_at""",
        (run_id, report, concept, target_id, f.kind, None if f.kind == "html" else value,
         status, evidence, json.dumps(pages), revision + 1, now))
    if f.kind == "html":
        stored = conn.execute("SELECT html FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
                              (run_id, REPORTS[report][2], f.row)).fetchone()
        # Unchanged text keeps its verified style; new text needs formatting.
        unchanged = stored is not None and stored[0] == (value or "")
        runs.upsert_notes_cell(conn, run_id=run_id, sheet=REPORTS[report][2], row=f.row,
                              label=f.label, html=value or "", evidence=evidence, source_pages=pages,
                              concept_uuid=target_id, style_source=None if unchanged else "unstyled")
        if status in {"user_override", "cleared"}:
            conn.execute("UPDATE notes_cells SET content_origin='human_modified' WHERE run_id=? AND sheet=? AND row=?",
                         (run_id, REPORTS[report][2], f.row))
    return revision + 1


def snapshot(conn, run_id: int) -> dict:
    standard, _, run = family(conn, run_id)
    requested = set((run.config or {}).get("reports_to_run", []))
    requested.update(r[0] for r in conn.execute("SELECT DISTINCT report_type FROM report_fields WHERE run_id=?", (run_id,)))
    reports = [report_view(conn, run_id, r) for r in REPORTS if r in requested]
    digest = hashlib.sha256(json.dumps(reports, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"reports": reports, "digest": digest, "filing_standard": standard}


def export_internal(conn, run_id: int, report: str, output: str) -> None:
    """Internal diagnostic workbook only. Filing uses the shared mTool patcher."""
    import openpyxl
    from utils.workbook_io import atomic_save_workbook
    standard, level, _ = family(conn, run_id)
    view = report_view(conn, run_id, report)
    wb = openpyxl.load_workbook(template_path(report, standard, level))
    try:
        ws = wb[view["sheet"]]
        for f in view["fields"]:
            value = f["value"]
            if f["kind"] == "html" and value:
                from notes.html_to_text import html_to_excel_text
                value = html_to_excel_text(value)
            cell = ws.cell(f["row"], 2, value)
            if isinstance(value, str):
                cell.data_type = "s"
        atomic_save_workbook(wb, output)
    finally:
        wb.close()


def diagnostic_workbooks(db_path, run_id: int, output_dir) -> dict[str, str]:
    """Re-render saved reports when a later canonical merge refreshes diagnostics."""
    from pathlib import Path
    from contextlib import closing
    paths = {}
    with closing(sqlite3.connect(db_path)) as conn:
        for (report,) in conn.execute("SELECT DISTINCT report_type FROM report_fields WHERE run_id=?", (run_id,)).fetchall():
            path = str(Path(output_dir) / f"{report}_filled.xlsx")
            export_internal(conn, run_id, report, path)
            paths[report] = path
    return paths
