"""Exercise every generated-template slot through canonical persistence/export.

This is an offline mapping audit, not source-extraction or native mTool QA.
Physical expectations come from the declared manifest coordinates and the
documented scalar/matrix layout, never concept_targets or exporter routing.
Every slot is reported, including non-inputs and unsupported outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from fastapi import HTTPException

from concept_model.bootstrap import _import_one
from concept_model.exporter import export_run_to_xlsx
from concept_model.facts_api import FactWrite, apply_fact
from concept_model.filing_targets import active_template_paths, persist_template_manifest, targets_for_template
from concept_model.notes_importer import import_notes_template
from concept_model.notes_parser import parse_notes_template
from concept_model.taxonomy_semantics import taxonomy_concept
from db.schema import init_db
from notes.persistence import overlay_notes_cells_into_workbook


def fingerprint(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def physical_coordinates(target, standard: str, level: str):
    """Expand scalar slots without consulting the production target resolver."""
    if target.value_kind == "html":
        return [(target.sheet, target.row, "B", "CY", level.title())]
    if target.col == "A":
        # Matrix section captions are actual A-column presentation slots,
        # not scalar values with comparative columns.
        return [(target.sheet, target.row, "A", "CY", level.title())]
    if target.dimensions.get("period"):
        # Independently check the fixed physical matrix block contract.
        start, stride = (6, 18) if standard == "clbg" else (6, 24)
        block = (target.row - start) // stride
        if block not in range(4 if level == "group" else 2):
            raise ValueError(f"Unrecognized matrix block row {target.row}")
        period = "CY" if block % 2 == 0 else "PY"
        scope = "Group" if level == "group" and block < 2 else "Company"
        if (period, scope) != (target.dimensions["period"], target.dimensions["entity_scope"]):
            raise ValueError("Declared matrix dimensions disagree with physical block")
        return [(target.sheet, target.row, target.col, period, scope)]
    columns = [("B", "CY", "Company"), ("C", "PY", "Company")]
    if level == "group":
        columns = [("B", "CY", "Group"), ("C", "PY", "Group"),
                   ("D", "CY", "Company"), ("E", "PY", "Company")]
    return [(target.sheet, target.row, col, period, scope) for col, period, scope in columns]


def prose_sentinel(number: int):
    html = (f"<h3>Field {number}</h3><p>Amount {number} &amp; evidence.</p>"
            f"<ul><li>First {number}</li><li>Second {number}</li></ul>"
            f"<table><tr><th>Category</th><th>Value</th></tr>"
            f"<tr><td>Class {number}</td><td>{number}</td></tr></table>")
    # Literal expected flattening, independent of the production HTML renderer.
    plain = (f"Field {number}\n\nAmount {number} & evidence.\n\n"
             f"- First {number}\n- Second {number}\n\n"
             f"Category | Value\nClass {number} | {number}")
    return html, plain


def snapshot(workbook):
    return {
        (sheet.title, cell.coordinate): {
            # Empty default cells acquire an explicit all-zero style array
            # when populated. That is serialization normalization, not drift.
            "value": cell.value, "style": list(cell._style) if cell._style is not None else [0] * 9,
            "locked": cell.protection.locked, "hidden": cell.protection.hidden,
        }
        for sheet in workbook for row in sheet for cell in row
    }


def import_for_audit(db: Path, path: Path, level: str, targets):
    if targets and all(target.value_kind == "html" for target in targets):
        template_id, nodes = parse_notes_template(str(path), targets[0].sheet)
        import_notes_template(db, template_id, nodes)
        persist_template_manifest(db, path)
        return template_id
    return _import_one(db, path, level)


def audit_templates(paths: list[Path], output: Path) -> dict:
    """Run the audit in isolated files. Return summary; retain row-level evidence."""
    output.mkdir(parents=True, exist_ok=True)
    db = output / "canonical-audit.db"
    if db.exists():
        raise FileExistsError(f"Use a fresh evidence directory; database exists: {db}")
    init_db(db)
    slot_records, physical_records, template_records = [], [], []
    serial = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        standard, level = path.parent.parent.name.removeprefix("XBRL-template-").lower(), path.parent.name.lower()
        before_hash = fingerprint(path)
        template_id, targets = targets_for_template(path)
        import_for_audit(db, path, level, targets)
        wb = openpyxl.load_workbook(path, data_only=False)
        original = snapshot(wb)
        protection = {s.title: str(s.protection) for s in wb}
        merges = {s.title: str(s.merged_cells) for s in wb}
        work = output / "workbooks" / relative
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, work)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        config = {"filing_standard": standard, "filing_level": level}
        run_id = conn.execute(
            "INSERT INTO runs(created_at,pdf_filename,status,run_config_json) VALUES (?,?,?,?)",
            ("2026-10-03T00:00:00Z", "offline-field-audit.pdf", "running", json.dumps(config)),
        ).lastrowid
        conn.commit()
        expected_by_identity = {}
        local_records = []
        for target in targets:
            slot = asdict(target) | {"path": relative, "writable": target.writable,
                                      "run_id": run_id, "physical_results": []}
            slot_records.append(slot)
            concept_type = taxonomy_concept(target.taxonomy_element_id) if target.taxonomy_element_id else None
            slot["data_type"] = concept_type.data_type if concept_type else None
            slot["unit_check"] = "identity_only_no_denominator_translation"
            try:
                coordinates = physical_coordinates(target, standard, level)
            except Exception as exc:
                slot["status"] = "unresolved_physical_inventory"
                slot["error"] = str(exc)
                continue
            for sheet, row, col, period, scope in coordinates:
                serial += 1
                record = {"slot_target_id": target.target_id, "path": relative,
                          "template_id": template_id, "concept_uuid": target.canonical_target_id,
                          "taxonomy_element_id": target.taxonomy_element_id,
                          "category": {k: v for k, v in target.dimensions.items()
                                       if k not in {"period", "entity_scope"}},
                          "slot_role": target.slot_role, "value_kind": target.value_kind,
                          "data_type": slot["data_type"], "unit_check": slot["unit_check"],
                          "sheet": sheet, "cell": f"{col}{row}", "period": period,
                          "entity_scope": scope, "writable": target.writable}
                record["period_scope_basis"] = ("presentation_only_no_fact_dimension" if col == "A"
                                                else "independent_physical_layout_contract")
                slot["physical_results"].append(len(physical_records))
                physical_records.append(record)
                local_records.append(record)
                current = wb[sheet][f"{col}{row}"].value
                record["original_value"] = current
                if not target.writable:
                    node = conn.execute("SELECT kind FROM concept_nodes WHERE concept_uuid=?",
                                        (target.canonical_target_id,)).fetchone()
                    if target.value_kind == "html" or node:
                        body = FactWrite(concept_uuid=target.canonical_target_id, period=period,
                                         entity_scope=scope, value=999999, actor="offline-field-audit")
                        if target.value_kind == "html":
                            body = FactWrite(concept_uuid=target.canonical_target_id, html="<p>Forbidden.</p>",
                                             sheet=sheet, row=row, label=target.label)
                        try:
                            apply_fact(conn, run_id, body, commit=False)
                            record["guard"] = "unexpectedly_accepted"
                            conn.rollback()
                        except HTTPException as exc:
                            record["guard"] = "rejected"
                            record["guard_reason"] = str(exc.detail)
                    else:
                        record["guard"] = "not_applicable_physical_alias_or_metadata"
                    record["status"] = "pending_preservation"
                    continue
                identity = (target.canonical_target_id, period, scope)
                if identity not in expected_by_identity:
                    expected_by_identity[identity] = serial * 17 + 1
                value = expected_by_identity[identity]
                record["expected"] = value
                record["database_identity"] = list(identity)
                try:
                    if target.value_kind == "html":
                        html, plain = prose_sentinel(value)
                        record["expected"] = plain
                        record["expected_html"] = html
                        body = FactWrite(concept_uuid=target.canonical_target_id, html=html,
                                         sheet=sheet, row=row, label=target.label, actor="offline-field-audit")
                    else:
                        body = FactWrite(concept_uuid=target.canonical_target_id, period=period,
                                         entity_scope=scope, value=value, actor="offline-field-audit",
                                         dimensions=record["category"])
                    apply_fact(conn, run_id, body, commit=False)
                    conn.commit()
                    if target.value_kind == "html":
                        stored = conn.execute("SELECT html FROM notes_cells WHERE run_id=? AND concept_uuid=?",
                                              (run_id, target.canonical_target_id)).fetchone()
                        record["database_pass"] = bool(stored and stored[0] == record["expected_html"])
                    else:
                        stored = conn.execute("SELECT value FROM run_concept_facts WHERE run_id=? AND concept_uuid=? "
                                              "AND period=? AND entity_scope=?", (run_id, *identity)).fetchall()
                        record["database_pass"] = len(stored) == 1 and stored[0][0] == value
                    record["status"] = "pending_readback"
                except Exception as exc:
                    conn.rollback()
                    record["status"] = "canonical_write_rejected"
                    record["error"] = str(getattr(exc, "detail", exc))
        conn.close()
        export_error = None
        try:
            if any(t.writable and t.value_kind == "numeric" for t in targets):
                export_run_to_xlsx(db, run_id, work, filing_level=level, template_id=template_id)
            if any(t.writable and t.value_kind == "html" for t in targets):
                overlay = overlay_notes_cells_into_workbook(xlsx_path=work, run_id=run_id,
                                                            db_path=str(db), filing_level=level)
                if overlay != work:
                    shutil.copyfile(overlay, work)
                    Path(overlay).unlink()
        except Exception as exc:
            export_error = str(exc)
        final = openpyxl.load_workbook(work, data_only=False)
        after = snapshot(final)
        formula_changes = [f"{sheet}!{cell}" for (sheet, cell), state in original.items()
                           if isinstance(state["value"], str) and state["value"].startswith("=")
                           and after.get((sheet, cell), {}).get("value") != state["value"]]
        style_changes = [f"{sheet}!{cell}" for (sheet, cell), state in original.items()
                         if any(after.get((sheet, cell), {}).get(k) != state[k]
                                for k in ("style", "locked", "hidden"))]
        protection_pass = protection == {s.title: str(s.protection) for s in final}
        merges_pass = merges == {s.title: str(s.merged_cells) for s in final}
        for record in local_records:
            record["actual"] = final[record["sheet"]][record["cell"]].value
            if record["status"] == "pending_readback":
                record["status"] = ("passed" if record["actual"] == record["expected"] and record["database_pass"]
                                    else "readback_mismatch") if export_error is None else "export_failed"
            elif record["status"] == "pending_preservation":
                preserved = record["actual"] == record["original_value"]
                record["status"] = "preserved" if preserved and record["guard"] != "unexpectedly_accepted" else "protection_failed"
        for slot in slot_records:
            if slot["path"] != relative or "status" in slot:
                continue
            results = [physical_records[i]["status"] for i in slot["physical_results"]]
            slot["status"] = "passed" if all(s == "passed" for s in results) else (
                "preserved" if all(s == "preserved" for s in results) else "needs_review")
        source_unchanged = before_hash == fingerprint(path)
        template_records.append({"path": relative, "template_id": template_id, "sha256": before_hash,
                                 "output_sha256": fingerprint(work), "slots": len(targets),
                                 "writable_slots": sum(t.writable for t in targets), "source_unchanged": source_unchanged,
                                 "formula_changes": formula_changes, "style_changes": style_changes,
                                 "sheet_protection_preserved": protection_pass, "merged_cells_preserved": merges_pass,
                                 "export_error": export_error})
        with sqlite3.connect(db) as conn:
            clean = (export_error is None and all(r["status"] in {"passed", "preserved"} for r in local_records)
                     and not formula_changes and not style_changes and protection_pass and merges_pass and source_unchanged)
            conn.execute("UPDATE runs SET status=?,ended_at=? WHERE id=?",
                         ("completed" if clean else "completed_with_errors", "2026-10-03T00:00:00Z", run_id))
        wb.close()
        final.close()
        print(f"{relative}: {len(targets)} slots", flush=True)
    summary = {"templates": len(paths), "declared_slots": len(slot_records),
               "writable_slots": sum(s["writable"] for s in slot_records),
               "nonwritable_slots": sum(not s["writable"] for s in slot_records),
               "declared_statuses": dict(Counter(s["status"] for s in slot_records)),
               "families": {standard: {"slots": sum(s["template_id"].startswith(standard + "-") for s in slot_records),
                                        "writable": sum(s["writable"] and s["template_id"].startswith(standard + "-") for s in slot_records)}
                            for standard in ("mfrs", "mpers", "clbg")},
               "physical_destinations": len(physical_records),
               "physical_statuses": dict(Counter(r["status"] for r in physical_records)),
               "calculation_validation": "not_run_unbalanced_mapping_sentinels",
               "native_workbooks": "not_tested_separate_harness",
               "source_extraction_and_model_accuracy": "not_tested",
               "notes_layout": "generated_plaintext_only_native_rich_rendering_not_tested",
               "templates_detail": template_records}
    for filename, records in (("declared-slots.jsonl", slot_records), ("physical-results.jsonl", physical_records)):
        with (output / filename).open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", action="append", help="Repository-relative template path; default all active templates")
    args = parser.parse_args()
    paths = [ROOT / p for p in args.template] if args.template else active_template_paths(ROOT)
    summary = audit_templates(paths, args.output.resolve())
    print(json.dumps({k: v for k, v in summary.items() if k != "templates_detail"}, indent=2))
    failure = any(s not in {"passed", "preserved"} for s in summary["declared_statuses"])
    failure |= any(t["formula_changes"] or t["style_changes"] or not t["source_unchanged"]
                   or not t["sheet_protection_preserved"] or not t["merged_cells_preserved"]
                   for t in summary["templates_detail"])
    return int(failure)


if __name__ == "__main__":
    raise SystemExit(main())
