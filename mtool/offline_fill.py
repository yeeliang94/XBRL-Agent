"""Offline mTool template filler — zip-surgery spike (docs/PLAN-mtool-offline-patch-spike.md).

Fills numeric values into a *closed* mTool-generated workbook without Excel,
by rewriting only the target worksheet XML inside the xlsx zip. Every other
zip entry is copied verbatim. Phase-1 ground truth: openpyxl load/save
corrupts the mTool package and full XML reserialization breaks namespaces,
so writing is targeted text surgery; reading uses ElementTree (safe — only
re-serializing is destructive).

Deliberately self-contained and stdlib-only (no openpyxl, no repo imports):
the file must travel to an enterprise Windows environment as a single script
and run on any Python >= 3.9 without pip.

Usage:
    python offline_fill.py inspect --workbook template.xlsx [--sheet NAME]
    python offline_fill.py fill --workbook template.xlsx --input fill.json \
        --output filled.xlsx [--report report.json] [--force-recalc] [--dry-run]

Input file shape (docs/PLAN-mtool-offline-patch-spike.md for the contract):
    {
      "sheets": {"SOFP-Sub-CuNonCu": {"label_column": "A",
                                       "columns": {"current_year": "B"}}},
      "writes": [
        {"sheet": "SOFP-Sub-CuNonCu", "label": "Freehold land",
         "column_role": "current_year", "value": 1000},
        {"sheet": "SOFP-Sub-CuNonCu", "cell": "C15", "value": 2500}
      ]
    }
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

FUZZY_THRESHOLD = 0.90
_TEXT_CELL_TYPES = {"s", "str", "inlineStr", "b", "e"}


# ---------------------------------------------------------------- utilities

def normalize_label(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(":").strip()
    return text.casefold()


def col_to_idx(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def split_ref(addr: str):
    m = re.fullmatch(r"([A-Z]{1,3})([1-9]\d*)", addr)
    if not m:
        raise ValueError(f"invalid cell reference: {addr!r}")
    return m.group(1), int(m.group(2))


def format_value(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"value must be a number, got {value!r}")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"value must be finite, got {value!r}")
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------- zip reading

def load_workbook_entries(path: str):
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        data = {i.filename: zf.read(i.filename) for i in infos}
        comment = zf.comment
    return infos, data, comment


def get_sheet_paths(data: dict) -> dict:
    """Map sheet name -> zip entry path, via workbook.xml + its rels."""
    wb = ET.fromstring(data["xl/workbook.xml"])
    rels = ET.fromstring(data["xl/_rels/workbook.xml.rels"])
    rid_to_target = {}
    for rel in rels:
        if _local(rel.tag) == "Relationship":
            rid_to_target[rel.get("Id")] = rel.get("Target")
    out = {}
    for el in wb.iter():
        if _local(el.tag) != "sheet":
            continue
        rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), None)
        target = rid_to_target.get(rid)
        if not target:
            continue
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        out[el.get("name")] = path
    return out


def get_shared_strings(data: dict) -> list:
    raw = data.get("xl/sharedStrings.xml")
    if not raw:
        return []
    out = []
    for si in ET.fromstring(raw):
        texts = [t.text or "" for t in si.iter() if _local(t.tag) == "t"]
        out.append("".join(texts))
    return out


def read_sheet_cells(sheet_xml: bytes, sst: list) -> dict:
    """{row_number: {col_letter: (kind, text)}} — kinds: F formula, S text,
    N number, E styled-empty. Absent cells simply don't appear."""
    root = ET.fromstring(sheet_xml)
    rows = {}
    for row_el in root.iter():
        if _local(row_el.tag) != "row":
            continue
        r_attr = row_el.get("r")
        if r_attr is None:
            continue
        cells = {}
        for c in row_el:
            if _local(c.tag) != "c":
                continue
            ref = c.get("r")
            if not ref:
                continue
            col = split_ref(ref)[0]
            t = c.get("t")
            f_el = v_el = is_el = None
            for child in c:
                name = _local(child.tag)
                if name == "f":
                    f_el = child
                elif name == "v":
                    v_el = child
                elif name == "is":
                    is_el = child
            if f_el is not None:
                cells[col] = ("F", v_el.text if v_el is not None else "")
            elif t == "s" and v_el is not None:
                try:
                    text = sst[int(v_el.text)]
                except (ValueError, TypeError, IndexError):
                    text = ""
                cells[col] = ("S", text)
            elif t == "inlineStr" and is_el is not None:
                text = "".join(t_el.text or "" for t_el in is_el.iter()
                               if _local(t_el.tag) == "t")
                cells[col] = ("S", text)
            elif t == "str" and v_el is not None:
                cells[col] = ("S", v_el.text or "")
            elif v_el is not None:
                cells[col] = ("N", v_el.text or "")
            else:
                cells[col] = ("E", "")
        rows[int(r_attr)] = cells
    return rows


# ---------------------------------------------------------------- resolution

def build_label_map(rows: dict, label_column: str) -> dict:
    """{normalized label: [(row_number, raw_label), ...]}"""
    out = {}
    for row_num in sorted(rows):
        kind_text = rows[row_num].get(label_column)
        if not kind_text or kind_text[0] != "S":
            continue
        raw = kind_text[1]
        norm = normalize_label(raw)
        if norm:
            out.setdefault(norm, []).append((row_num, raw))
    return out


def resolve_row(label: str, label_map: dict) -> dict:
    """-> {status: resolved|ambiguous|unresolved, row?, matched_label?, ratio?}"""
    norm = normalize_label(label)
    hits = label_map.get(norm)
    if hits:
        if len(hits) > 1:
            return {"status": "ambiguous",
                    "detail": f"label matches rows {[r for r, _ in hits]}"}
        return {"status": "resolved", "row": hits[0][0],
                "matched_label": hits[0][1], "ratio": 1.0}
    scored = sorted(
        ((difflib.SequenceMatcher(None, norm, key).ratio(), key)
         for key in label_map),
        reverse=True,
    )
    if not scored or scored[0][0] < FUZZY_THRESHOLD:
        return {"status": "unresolved", "detail": "no match above threshold"}
    best_ratio, best_key = scored[0]
    if len(scored) > 1 and scored[1][0] == best_ratio:
        return {"status": "unresolved",
                "detail": f"fuzzy tie at ratio {best_ratio:.2f}"}
    hits = label_map[best_key]
    if len(hits) > 1:
        return {"status": "ambiguous",
                "detail": f"fuzzy match on duplicated label rows {[r for r, _ in hits]}"}
    return {"status": "resolved", "row": hits[0][0],
            "matched_label": hits[0][1], "ratio": round(best_ratio, 3)}


# ---------------------------------------------------------------- patching

class PrefixedSheetError(Exception):
    """Sheet XML uses a namespace prefix — text-level inserts would corrupt it."""


def _cell_pattern(addr: str):
    return re.compile(
        r'<c\b[^>]*\br="%s"[^>]*(?:/>|>.*?</c>)' % re.escape(addr), re.DOTALL)


def _row_pattern(row_num: int):
    return re.compile(
        r'<row\b[^>]*\br="%d"[^>]*(?:/>|>.*?</row>)' % row_num, re.DOTALL)


def _attr(opening: str, name: str):
    m = re.search(r'\b%s="([^"]*)"' % name, opening)
    return m.group(1) if m else None


def _rebuild_cell(addr: str, style, value_str: str) -> str:
    s_part = f' s="{style}"' if style is not None else ""
    return f'<c r="{addr}"{s_part}><v>{value_str}</v></c>'


def patch_cell_in_sheet(xml: str, addr: str, value_str: str):
    """Apply one write. Returns (new_xml, action); action is one of
    replaced | expanded | rebuilt | type_changed | inserted_cell |
    inserted_row | formula_skipped."""
    if "<sheetData" not in xml:
        if re.search(r"<\w+:sheetData\b", xml):
            raise PrefixedSheetError(
                "sheet XML uses a namespace prefix; refusing to patch")
        raise ValueError("no <sheetData> element found in sheet XML")

    col, row_num = split_ref(addr)
    cell_m = _cell_pattern(addr).search(xml)
    if cell_m:
        cell_xml = cell_m.group(0)
        if re.search(r"<f[ >/]", cell_xml):
            return xml, "formula_skipped"
        opening = re.match(r"<c\b[^>]*?/?>", cell_xml).group(0)
        t = _attr(opening, "t")
        style = _attr(opening, "s")
        if t in _TEXT_CELL_TYPES:
            # Replacing <v> on a t="s" cell would write a shared-string INDEX,
            # not a number — the cell must be rebuilt typeless.
            new_cell = _rebuild_cell(addr, style, value_str)
            action = "type_changed"
        elif cell_xml.endswith("/>"):
            new_cell = cell_xml[:-2] + f"><v>{value_str}</v></c>"
            action = "expanded"
        elif re.search(r"<v\b[^>]*>.*?</v>", cell_xml, re.DOTALL):
            new_cell = re.sub(r"<v\b[^>]*>.*?</v>", f"<v>{value_str}</v>",
                              cell_xml, count=1, flags=re.DOTALL)
            action = "replaced"
        else:
            new_cell = _rebuild_cell(addr, style, value_str)
            action = "rebuilt"
        return xml[:cell_m.start()] + new_cell + xml[cell_m.end():], action

    new_cell = _rebuild_cell(addr, None, value_str)
    row_m = _row_pattern(row_num).search(xml)
    if row_m:
        row_xml = row_m.group(0)
        if row_xml.endswith("/>"):
            new_row = row_xml[:-2] + f">{new_cell}</row>"
        else:
            insert_at = len(row_xml) - len("</row>")
            target_idx = col_to_idx(col)
            for m in re.finditer(r'<c\b[^>]*\br="([A-Z]{1,3})\d+"', row_xml):
                if col_to_idx(m.group(1)) > target_idx:
                    insert_at = m.start()
                    break
            new_row = row_xml[:insert_at] + new_cell + row_xml[insert_at:]
        return xml[:row_m.start()] + new_row + xml[row_m.end():], "inserted_cell"

    new_row = f'<row r="{row_num}">{new_cell}</row>'
    insert_at = None
    for m in re.finditer(r'<row\b[^>]*\br="(\d+)"', xml):
        if int(m.group(1)) > row_num:
            insert_at = m.start()
            break
    if insert_at is None:
        close_m = re.search(r"</sheetData>", xml)
        if close_m:
            insert_at = close_m.start()
        else:
            empty_m = re.search(r"<sheetData\s*/>", xml)
            if not empty_m:
                raise ValueError(f"cannot locate insertion point for {addr}")
            return (xml[:empty_m.start()] + f"<sheetData>{new_row}</sheetData>"
                    + xml[empty_m.end():], "inserted_row")
    return xml[:insert_at] + new_row + xml[insert_at:], "inserted_row"


def set_full_calc_on_load(workbook_xml: str):
    """Set fullCalcOnLoad="1" on an EXISTING <calcPr>. Returns (xml, found).
    Only touches an existing element — synthesizing one means picking a
    schema-ordered insertion point, which is the reserialization trap."""
    m = re.search(r"<calcPr\b[^>]*/?>", workbook_xml)
    if not m:
        return workbook_xml, False
    tag = m.group(0)
    if 'fullCalcOnLoad="1"' in tag:
        return workbook_xml, True
    if "fullCalcOnLoad" in tag:
        new_tag = re.sub(r'fullCalcOnLoad="[^"]*"', 'fullCalcOnLoad="1"', tag)
    elif tag.endswith("/>"):
        new_tag = tag[:-2] + ' fullCalcOnLoad="1"/>'
    else:
        new_tag = tag[:-1] + ' fullCalcOnLoad="1">'
    return workbook_xml[:m.start()] + new_tag + workbook_xml[m.end():], True


def write_patched_zip(src_path: str, dst_path: str, replacements: dict):
    """Copy every entry in original order/metadata; swap in patched bytes."""
    with zipfile.ZipFile(src_path) as zin, \
            zipfile.ZipFile(dst_path, "w") as zout:
        zout.comment = zin.comment
        for info in zin.infolist():
            payload = replacements.get(info.filename, zin.read(info.filename))
            ni = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            ni.compress_type = info.compress_type
            ni.external_attr = info.external_attr
            ni.internal_attr = info.internal_attr
            ni.create_system = info.create_system
            zout.writestr(ni, payload)


def verify_values(path: str, checks: list) -> list:
    """checks: [(sheet_entry_path, addr, expected_value_str)] -> mismatches."""
    _, data, _ = load_workbook_entries(path)
    mismatches = []
    for entry_path, addr, expected in checks:
        xml = data.get(entry_path, b"").decode("utf-8", errors="replace")
        m = _cell_pattern(addr).search(xml)
        found = None
        if m:
            v = re.search(r"<v\b[^>]*>(.*?)</v>", m.group(0), re.DOTALL)
            if v:
                found = v.group(1)
        try:
            ok = found is not None and float(found) == float(expected)
        except ValueError:
            ok = False
        if not ok:
            mismatches.append({"entry": entry_path, "cell": addr,
                               "expected": expected, "found": found})
    return mismatches


# ---------------------------------------------------------------- input file

def validate_input(doc: dict) -> list:
    errors = []
    if not isinstance(doc, dict) or not isinstance(doc.get("writes"), list) \
            or not doc.get("writes"):
        return ["input must be an object with a non-empty 'writes' list"]
    sheets_cfg = doc.get("sheets", {})
    seen_targets = set()
    for i, w in enumerate(doc["writes"]):
        where = f"writes[{i}]"
        if not isinstance(w, dict):
            errors.append(f"{where}: must be an object")
            continue
        if not w.get("sheet"):
            errors.append(f"{where}: missing 'sheet'")
        has_label = "label" in w
        has_cell = "cell" in w
        if has_label == has_cell:
            errors.append(f"{where}: exactly one of 'label' or 'cell' required")
        elif has_label:
            role = w.get("column_role")
            if not role:
                errors.append(f"{where}: 'label' writes need 'column_role'")
            else:
                columns = sheets_cfg.get(w.get("sheet"), {}).get("columns", {})
                if role not in columns:
                    errors.append(
                        f"{where}: column_role {role!r} not configured for "
                        f"sheet {w.get('sheet')!r}")
        if has_cell:
            try:
                split_ref(str(w["cell"]).upper())
            except ValueError as exc:
                errors.append(f"{where}: {exc}")
            target = (w.get("sheet"), str(w.get("cell", "")).upper())
            if target in seen_targets:
                errors.append(f"{where}: duplicate write to {target}")
            seen_targets.add(target)
        value = w.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{where}: 'value' must be a JSON number, "
                          f"got {value!r}")
    return errors


# ---------------------------------------------------------------- commands

def run_fill(args) -> int:
    # utf-8-sig, not utf-8: PowerShell (the operator's shell on the mTool box)
    # writes JSON with a UTF-8 BOM, which a plain utf-8 reader rejects.
    with open(args.input, encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    input_errors = validate_input(doc)
    if input_errors:
        for e in input_errors:
            print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2

    _, data, _ = load_workbook_entries(args.workbook)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    sheets_cfg = doc.get("sheets", {})

    report = {
        "workbook": args.workbook,
        "output": None if args.dry_run else args.output,
        "dry_run": bool(args.dry_run),
        "written": [], "fuzzy_matched": [], "skipped_formula": [],
        "type_changed": [],
        "unresolved": [], "ambiguous": [], "mismatches": [], "errors": [],
        "force_recalc": None,
    }

    # Strict mode refuses fuzzy matches (writes them to `unresolved` instead of
    # applying). CLI --strict OR a doc-level "strict": true (the exporter sets
    # this on machine-generated docs, where a non-exact label is a bug, not a
    # typo to forgive). Hand-authored operator runs default lenient.
    strict = bool(getattr(args, "strict", False) or doc.get("strict"))
    report["strict"] = strict

    label_maps = {}
    patched_xml = {}
    verify_targets = []
    resolved_targets = set()

    for i, w in enumerate(doc["writes"]):
        sheet = w["sheet"]
        entry_path = sheet_paths.get(sheet)
        base = {"index": i, "sheet": sheet, "label": w.get("label"),
                "column_role": w.get("column_role"), "value": w["value"]}
        if entry_path is None:
            report["errors"].append({**base, "error":
                f"sheet {sheet!r} not found; workbook has "
                f"{sorted(sheet_paths)}"})
            continue

        if "cell" in w:
            addr = str(w["cell"]).upper()
            base["cell"] = addr
        else:
            if sheet not in label_maps:
                cfg = sheets_cfg.get(sheet, {})
                rows = read_sheet_cells(data[entry_path], sst)
                label_maps[sheet] = build_label_map(
                    rows, cfg.get("label_column", "A"))
            res = resolve_row(w["label"], label_maps[sheet])
            if res["status"] == "ambiguous":
                report["ambiguous"].append({**base, **res})
                continue
            if res["status"] == "unresolved":
                report["unresolved"].append({**base, **res})
                continue
            if strict and res["status"] == "resolved" and res["ratio"] < 1.0:
                report["unresolved"].append({**base, **res,
                    "detail": f"strict mode: fuzzy match "
                              f"(similarity {res['ratio']}) refused; "
                              f"would have matched {res['matched_label']!r}"})
                continue
            col = sheets_cfg[sheet]["columns"][w["column_role"]]
            addr = f"{col}{res['row']}"
            base.update(cell=addr, matched_label=res["matched_label"],
                        ratio=res["ratio"])
        target = (sheet, addr)
        if target in resolved_targets:
            report["errors"].append({**base, "error":
                f"duplicate write to {sheet}!{addr} after resolution"})
            continue
        resolved_targets.add(target)

        xml = patched_xml.get(entry_path) or data[entry_path].decode("utf-8")
        try:
            value_str = format_value(w["value"])
            xml, action = patch_cell_in_sheet(xml, addr, value_str)
        except (PrefixedSheetError, ValueError) as exc:
            report["errors"].append({**base, "error": str(exc)})
            continue
        base["action"] = action
        if action == "formula_skipped":
            report["skipped_formula"].append(base)
            continue
        patched_xml[entry_path] = xml
        if action == "type_changed":
            report["type_changed"].append(base)
        if base.get("ratio") is not None and base["ratio"] < 1.0:
            report["fuzzy_matched"].append(base)
        report["written"].append(base)
        verify_targets.append((entry_path, addr, value_str))

    if args.force_recalc:
        wb_xml, found = set_full_calc_on_load(
            data["xl/workbook.xml"].decode("utf-8"))
        report["force_recalc"] = {"requested": True, "calcPr_found": found}
        if found:
            patched_xml["xl/workbook.xml"] = wb_xml
        else:
            report["errors"].append(
                {"error": "no <calcPr> in workbook.xml; fullCalcOnLoad "
                          "not set"})

    if not args.dry_run:
        replacements = {p: x.encode("utf-8") for p, x in patched_xml.items()}
        write_patched_zip(args.workbook, args.output, replacements)
        report["mismatches"] = verify_values(args.output, verify_targets)

    degraded = any(report[k] for k in
                   ("skipped_formula", "type_changed", "unresolved",
                    "ambiguous", "mismatches", "errors"))
    report["status"] = "degraded" if degraded else "ok"

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    _print_summary(report)
    return 1 if degraded else 0


def _print_summary(report: dict):
    print(f"status: {report['status']}"
          + (" (dry run)" if report["dry_run"] else ""))
    print(f"  written:         {len(report['written'])}")
    print(f"  fuzzy_matched:   {len(report['fuzzy_matched'])}")
    for e in report["fuzzy_matched"]:
        print(f"    - {e['sheet']} {e['label']!r} -> "
              f"{e['matched_label']!r} (similarity {e['ratio']}) "
              f"REVIEW: verify this is the intended row")
    for key in ("skipped_formula", "type_changed", "unresolved",
                "ambiguous", "mismatches", "errors"):
        entries = report[key]
        print(f"  {key + ':':<16} {len(entries)}")
        for e in entries:
            label = e.get("label") or e.get("cell") or ""
            detail = e.get("detail") or e.get("error") or e.get("found", "")
            print(f"    - {e.get('sheet', '')} {label!r} {detail}")
    if report["force_recalc"]:
        print(f"  force_recalc:    calcPr_found="
              f"{report['force_recalc']['calcPr_found']}")


def run_inspect(args) -> int:
    _, data, _ = load_workbook_entries(args.workbook)
    sheet_paths = get_sheet_paths(data)
    if not args.sheet:
        print("sheets:")
        for name, path in sheet_paths.items():
            print(f"  {name}  ({path})")
        return 0
    entry_path = sheet_paths.get(args.sheet)
    if entry_path is None:
        print(f"sheet {args.sheet!r} not found; workbook has "
              f"{sorted(sheet_paths)}", file=sys.stderr)
        return 2
    sst = get_shared_strings(data)
    rows = read_sheet_cells(data[entry_path], sst)
    label_col = args.label_column
    print(f"{args.sheet} — kinds: F formula, S text, N number, E styled-empty")
    for row_num in sorted(rows):
        cells = rows[row_num]
        label = cells.get(label_col, ("", ""))[1] if \
            cells.get(label_col, ("", ""))[0] == "S" else ""
        others = " ".join(
            f"{col}:{kind}" for col, (kind, _) in sorted(
                cells.items(), key=lambda kv: col_to_idx(kv[0]))
            if col != label_col)
        print(f"  {row_num:>4} | {label[:60]:<60} | {others}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="offline_fill",
        description="Fill numeric values into a closed mTool workbook "
                    "via zip surgery (no Excel required).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="list sheets / dump labels")
    p_inspect.add_argument("--workbook", required=True)
    p_inspect.add_argument("--sheet")
    p_inspect.add_argument("--label-column", default="A", dest="label_column")

    p_fill = sub.add_parser("fill", help="apply writes from an input file")
    p_fill.add_argument("--workbook", required=True)
    p_fill.add_argument("--input", required=True)
    p_fill.add_argument("--output")
    p_fill.add_argument("--report")
    p_fill.add_argument("--force-recalc", action="store_true",
                        dest="force_recalc")
    p_fill.add_argument("--strict", action="store_true", dest="strict",
                        help="refuse fuzzy label matches (report as unresolved)")
    p_fill.add_argument("--dry-run", action="store_true", dest="dry_run")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return run_inspect(args)
    if not args.dry_run and not args.output:
        parser.error("fill requires --output (or --dry-run)")
    if args.output and args.output == args.workbook:
        parser.error("--output must differ from --workbook "
                     "(never patch the original in place)")
    return run_fill(args)


if __name__ == "__main__":
    sys.exit(main())
