"""Resolve canonical filing facts onto an uploaded workbook.

This is the semantic seam between the application and mTool.  Taxonomy
concept identifiers and dimensions are the primary address.  Visible labels
and positional columns remain a compatibility fallback for workbooks that do
not expose those identifiers.

The module is deliberately read-only with respect to workbooks.  It produces
ordinary ``offline_fill`` instructions; the standard-library patcher remains
the only code that writes an mTool file.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from mtool.column_detect import (
    describe_template,
    detect_column_map,
    fingerprint_workbook,
)
from mtool.exporter import apply_column_map
from mtool.offline_fill import (
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    read_sheet_cells,
)


def index_workbook(data: dict) -> tuple[dict, dict]:
    """Return exact text occurrences and readable worksheet cells."""
    paths = get_sheet_paths(data)
    shared = get_shared_strings(data)
    occurrences: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    by_sheet: dict[str, dict] = {}
    for sheet, entry in paths.items():
        cells = read_sheet_cells(data[entry], shared)
        by_sheet[sheet] = cells
        for row, row_cells in cells.items():
            for col, (_kind, raw) in row_cells.items():
                text = (raw or "").strip()
                if text:
                    occurrences[text].append((sheet, int(row), col))
    return occurrences, by_sheet


def inspect_template(
    template_path: str,
    doc: dict[str, Any],
    *,
    data: dict | None = None,
    workbook_index: tuple[dict, dict] | None = None,
) -> dict[str, Any]:
    """Inspect one workbook and report its semantic filing capability."""
    if data is None:
        _, data, _ = load_workbook_entries(template_path)
    fingerprint = fingerprint_workbook(data)
    descriptor = describe_template(fingerprint)
    occurrences, cells_by_sheet = workbook_index or index_workbook(data)
    requested = len(doc.get("writes", []))
    addressable = 0
    missing_address = 0
    for write in doc.get("writes", []):
        address = write.get("semantic_address") or {}
        primary = address.get("primary_concept")
        if primary and occurrences.get(primary):
            addressable += 1
        elif primary:
            missing_address += 1

    meta = doc.get("meta", {})
    expected_standard = str(meta.get("filing_standard") or "").lower()
    expected_level = str(meta.get("filing_level") or "").lower()
    family_match = not descriptor or (
        (not expected_standard or expected_standard in descriptor.get("filing_standards", []))
        and (not expected_level or expected_level in descriptor.get("filing_levels", []))
    )
    if descriptor and descriptor.get("source") == "generated":
        expected_statements = {
            str(write.get("template_id") or "").split("-")[2].upper()
            for write in doc.get("writes", [])
            if len(str(write.get("template_id") or "").split("-")) >= 3
        }
        descriptor_tokens = {
            token for token in
            str(descriptor.get("name") or "").upper().split("-")
            if token
        }
        family_match = family_match and all(
            statement in descriptor_tokens for statement in expected_statements)
    generated = bool(
        descriptor and descriptor.get("source") == "generated" and family_match)
    semantic_source = "generated-targets" if generated else (
        "taxonomy-identifiers" if addressable else "legacy-labels")
    compatibility = "verified-generated" if generated else (
        "candidate-2.2" if addressable else "unverified")
    return {
        "fingerprint": fingerprint,
        "template": descriptor,
        "semantic_source": semantic_source,
        "mtool_compatibility": compatibility,
        "supported_mtool_version": "2.2",
        "filing_family_match": family_match,
        "requested": requested,
        "identifier_addressable": addressable,
        "identifier_missing": missing_address,
        "column_map": detect_column_map(
            template_path, doc, data=data, cells_by_sheet=cells_by_sheet
        ),
    }


def _dimension_column(
    occurrences: dict[str, list[tuple[str, int, str]]],
    dimensions: dict[str, str],
    *,
    sheet: str,
) -> str | None:
    if not dimensions:
        return None
    members = list(dimensions.values())
    columns: set[str] | None = None
    for member in members:
        found = {col for s, _row, col in occurrences.get(member, [])
                 if s == sheet}
        columns = found if columns is None else columns & found
    if columns and len(columns) == 1:
        return next(iter(columns))
    return None


def resolve_filing_doc(
    template_path: str,
    doc: dict[str, Any],
    *,
    data: dict | None = None,
    column_map: dict[str, dict[str, Any]] | None = None,
    workbook_index: tuple[dict, dict] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve writes and return ``(offline_doc, coverage_report)``.

    Generated repository templates use their exact canonical target hints.
    An SSM workbook is resolved by its embedded taxonomy identifiers; SOCIE
    adds the equity-component dimension to select the column.  Anything else
    goes through the existing label/column adapter and is explicitly reported
    as degraded.
    """
    if data is None:
        _, data, _ = load_workbook_entries(template_path)
    workbook_index = workbook_index or index_workbook(data)
    inspection = inspect_template(
        template_path, doc, data=data, workbook_index=workbook_index
    )
    occurrences, _ = workbook_index
    descriptor = inspection.get("template") or {}
    generated = descriptor.get("source") == "generated"
    if not inspection["filing_family_match"]:
        requested = len(doc.get("writes", []))
        report = {
            "status": "blocked", "requested": requested, "mapped": 0,
            "unmapped": requested, "ambiguous": 0, "legacy_label_writes": 0,
            "coverage_percent": 0.0 if requested else 100.0,
            "unresolved_writes": [{
                "detail": "template filing family or statement does not match the run; "
                          "expected sheets: " + ", ".join(sorted(doc.get("sheets", {})))
            }],
            "ambiguous_writes": [], "inspection": inspection,
        }
        return {**doc, "writes": []}, report
    detected = inspection["column_map"]
    cmap = column_map or {
        sheet: {
            "label_column": cfg.get("label_column"),
            "columns": cfg.get("columns", {}),
        }
        for sheet, cfg in detected.items()
    }

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    for write in doc.get("writes", []):
        hint = write.get("target_hint") or {}
        address = write.get("semantic_address") or {}
        primary = address.get("primary_concept")
        target: tuple[str, int, str] | None = None

        if generated and hint.get("sheet") and hint.get("row") and hint.get("col"):
            target = (hint["sheet"], int(hint["row"]), hint["col"])
        elif primary:
            candidates = list(occurrences.get(primary, []))
            same_sheet = [item for item in candidates
                          if item[0] == write.get("sheet")]
            if same_sheet:
                candidates = same_sheet
            dimensions = address.get("dimensions") or {}
            if dimensions:
                narrowed = []
                for sheet, row, _concept_col in candidates:
                    col = _dimension_column(occurrences, dimensions,
                                            sheet=sheet)
                    if col:
                        narrowed.append((sheet, row, col))
                candidates = narrowed
            else:
                role = write.get("column_role")
                candidates = [
                    (sheet, row, (cmap.get(sheet, {}).get("columns", {})
                                  .get(role)))
                    for sheet, row, _concept_col in candidates
                ]
                candidates = [item for item in candidates if item[2]]
            unique = list(dict.fromkeys(candidates))
            if len(unique) == 1:
                target = unique[0]
            elif len(unique) > 1:
                ambiguous.append({
                    "concept_uuid": write.get("concept_uuid"),
                    "primary_concept": primary,
                    "candidates": [f"{s}!{c}{r}" for s, r, c in unique],
                })
                continue

        if target:
            sheet, row, col = target
            item = {"sheet": sheet, "cell": f"{col}{row}",
                    "value": write["value"]}
            # Reverse ingest uses these non-patcher metadata fields to join a
            # resolved cell back to its canonical fact slot.  offline_fill
            # intentionally ignores unknown keys.
            for key in ("concept_uuid", "period", "entity_scope", "template_id"):
                if key in write:
                    item[key] = write[key]
            resolved.append(item)
        elif not primary or inspection["semantic_source"] == "legacy-labels":
            legacy.append(write)
        else:
            unresolved.append({
                "concept_uuid": write.get("concept_uuid"),
                "primary_concept": primary,
                "sheet": write.get("sheet"),
                "label": write.get("label"),
            })

    if legacy:
        legacy_doc = dict(doc)
        legacy_doc["writes"] = legacy
        legacy_ready = apply_column_map(legacy_doc, cmap)
        resolved.extend(legacy_ready["writes"])

    out = dict(doc)
    out["writes"] = resolved
    out["sheets"] = {} if not legacy else apply_column_map(
        {**doc, "writes": legacy}, cmap)["sheets"]
    out_meta = dict(doc.get("meta", {}))
    out_meta["columns_unresolved"] = False
    out["meta"] = out_meta

    requested = len(doc.get("writes", []))
    mapped = len(resolved)
    status = "ready"
    if unresolved or ambiguous:
        status = "blocked"
    elif legacy or inspection["mtool_compatibility"] == "candidate-2.2":
        status = "attention"
    report = {
        "status": status,
        "requested": requested,
        "mapped": mapped,
        "unmapped": len(unresolved),
        "ambiguous": len(ambiguous),
        "legacy_label_writes": len(legacy),
        "coverage_percent": round(mapped * 100 / requested, 1) if requested else 100.0,
        "unresolved_writes": unresolved,
        "ambiguous_writes": ambiguous,
        "inspection": inspection,
    }
    return out, report


__all__ = ["index_workbook", "inspect_template", "resolve_filing_doc"]
