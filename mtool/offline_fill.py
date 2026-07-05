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
from decimal import Decimal

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
        # Plain fixed-point, never scientific notation. repr() gives the
        # shortest round-trippable float; Decimal parses it exactly and
        # format("f") expands it, so an extreme magnitude (e.g. 1e21) writes
        # as digits, not "1e+21".
        return format(Decimal(repr(value)), "f")
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


# ------------------------------------------------------------- footnotes (notes)
#
# mTool stores prose-note *text blocks* NOT in the visible cell (which holds the
# literal trigger "[Text block added]") but in a hidden "+FootnoteTexts" sheet,
# keyed by a workbook defined name ``fn_N``:
#
#     <definedName name="fn_14">'Notes-Listofnotes'!$E$132</definedName>
#     +FootnoteTexts!A14 = fn_14   B14 = <visible sheet>   C14 = XHTML payload
#
# So the visible cell -> fn_N -> +FootnoteTexts row -> payload is the chain a
# notes filler walks. These readers are 100% programmatic (the fn_* live in the
# file), so no manual dump is ever needed to discover a template's note targets.
# Windows recon 2026-07-05 (mtool-notes-textblock-mechanism memory).

_FOOTNOTE_SHEET_HINT = "FootnoteTexts"


def _parse_defined_ref(ref: str):
    """Parse a defined-name value like ``'Notes-Listofnotes'!$E$132`` ->
    (sheet, cell). Handles single-quoted sheet names (with doubled ''
    escaping) and absolute ``$`` refs. Returns None if it isn't a single-cell
    reference on one sheet."""
    ref = (ref or "").strip()
    if "!" not in ref:
        return None
    sheet_part, cell_part = ref.rsplit("!", 1)
    sheet_part = sheet_part.strip()
    if sheet_part.startswith("'") and sheet_part.endswith("'"):
        sheet_part = sheet_part[1:-1].replace("''", "'")
    cell = cell_part.replace("$", "").strip()
    try:
        split_ref(cell)  # reject ranges / malformed refs
    except ValueError:
        return None
    return sheet_part, cell


def get_defined_names(data: dict, prefix: str = "fn_") -> dict:
    """{name: {sheet, cell, local_sheet_id}} for every ``<definedName>`` whose
    name starts with ``prefix`` and resolves to a single cell."""
    wb = ET.fromstring(data["xl/workbook.xml"])
    out = {}
    for el in wb.iter():
        if _local(el.tag) != "definedName":
            continue
        name = el.get("name") or ""
        if prefix and not name.startswith(prefix):
            continue
        parsed = _parse_defined_ref(el.text or "")
        if parsed:
            out[name] = {"sheet": parsed[0], "cell": parsed[1],
                         "local_sheet_id": el.get("localSheetId")}
    return out


def find_footnote_sheet(sheet_paths: dict) -> str | None:
    """Locate the hidden note-body sheet (canonically ``+FootnoteTexts``);
    tolerate a differing prefix by matching the ``FootnoteTexts`` stem."""
    if "+FootnoteTexts" in sheet_paths:
        return "+FootnoteTexts"
    for name in sheet_paths:
        if _FOOTNOTE_SHEET_HINT in name:
            return name
    return None


def read_footnote_rows(data: dict, sheet_paths: dict, sst: list,
                       footnote_sheet: str | None = None) -> dict:
    """Map ``fn_N`` -> {row, payload_col, payload_text, payload_populated}
    from the hidden footnote sheet. Column A holds the fn key; the payload is
    column C (per the mTool convention), falling back to the first text cell
    right of B."""
    footnote_sheet = footnote_sheet or find_footnote_sheet(sheet_paths)
    entry = sheet_paths.get(footnote_sheet) if footnote_sheet else None
    if entry is None:
        return {}
    rows = read_sheet_cells(data[entry], sst)
    out = {}
    c_idx = col_to_idx("C")
    b_idx = col_to_idx("B")
    for row_num, cells in rows.items():
        a = cells.get("A")
        if not a or a[0] != "S" or not a[1].strip():
            continue
        key = a[1].strip()
        payload_col = payload_text = None
        c_cell = cells.get("C")
        if c_cell and c_cell[0] == "S":
            payload_col, payload_text = "C", c_cell[1]
        else:
            for col in sorted((c for c in cells if col_to_idx(c) > b_idx),
                              key=col_to_idx):
                kind, text = cells[col]
                if kind == "S" and text.strip():
                    payload_col, payload_text = col, text
                    break
        out[key] = {"row": row_num, "payload_col": payload_col,
                    "payload_text": payload_text,
                    "payload_populated": bool(payload_text
                                              and payload_text.strip())}
    return out


def inspect_footnotes(data: dict) -> dict:
    """Build the full note-target map for a workbook: every ``fn_*`` defined
    name joined to its visible-row text (candidate label to match a canonical
    note against) and its ``+FootnoteTexts`` payload state. Read-only.

    Returns {footnote_sheet, targets: [...], orphan_payload_keys: [...]}.
    Each target: {key, sheet, cell, row, row_text, payload_col, payload_row,
    payload_populated, payload_len, has_payload_row}.
    """
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    defined = get_defined_names(data, "fn_")
    footnote_sheet = find_footnote_sheet(sheet_paths)
    fn_rows = read_footnote_rows(data, sheet_paths, sst, footnote_sheet)

    row_text_cache: dict[str, dict] = {}
    targets = []
    for key in sorted(defined, key=lambda k: (defined[k]["sheet"],
                                              int(re.sub(r"\D", "", k) or 0))):
        d = defined[key]
        sheet, cell = d["sheet"], d["cell"]
        _, row_num = split_ref(cell)
        entry = sheet_paths.get(sheet)
        if entry is not None:
            if sheet not in row_text_cache:
                row_text_cache[sheet] = read_sheet_cells(data[entry], sst)
            row_cells = row_text_cache[sheet].get(row_num, {})
        else:
            row_cells = {}
        # Every text cell in the visible row — the label lives in one of these
        # (mTool's observed layout puts labels left of the trigger cell); we
        # surface all of them so the mapping's label column is chosen from data.
        row_text = {col: txt for col, (kind, txt) in sorted(
            row_cells.items(), key=lambda kv: col_to_idx(kv[0]))
            if kind == "S" and txt.strip()}
        fn = fn_rows.get(key)
        targets.append({
            "key": key,
            "sheet": sheet,
            "cell": cell,
            "row": row_num,
            "row_text": row_text,
            "has_payload_row": fn is not None,
            "payload_row": fn["row"] if fn else None,
            "payload_col": fn["payload_col"] if fn else None,
            "payload_populated": bool(fn and fn["payload_populated"]),
            "payload_len": len(fn["payload_text"]) if fn and fn["payload_text"]
            else 0,
        })
    orphans = sorted(set(fn_rows) - set(defined))
    return {"footnote_sheet": footnote_sheet, "targets": targets,
            "orphan_payload_keys": orphans}


# ---------------------------------------------------------------- patching

class PrefixedSheetError(Exception):
    """Sheet XML uses a namespace prefix — text-level inserts would corrupt it."""


def _cell_pattern(addr: str):
    # Self-closing and paired cells are TWO distinct alternatives, NOT
    # `(?:/>|>.*?</c>)` after a shared `[^>]*`: there, greedy `[^>]*` eats the
    # `/` of a self-closing cell, the `/>` branch then fails, and `>.*?</c>`
    # swallows across following cells/rows to the next `</c>` (the recon
    # guide's self-closing-cell corruption). The lookahead asserts the ref
    # inside the tag; `[^>]*/>` can't cross a `>`, so it stays minimal.
    a = re.escape(addr)
    return re.compile(
        r'<c\b(?=[^>]*\br="%s")[^>]*/>'
        r'|<c\b(?=[^>]*\br="%s")[^>]*>.*?</c>' % (a, a), re.DOTALL)


def _row_pattern(row_num: int):
    return re.compile(
        r'<row\b(?=[^>]*\br="%d")[^>]*/>'
        r'|<row\b(?=[^>]*\br="%d")[^>]*>.*?</row>' % (row_num, row_num),
        re.DOTALL)


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


# ------------------------------------------------------- footnote (note) writing
#
# The write half of the notes mechanism (readers above): drop our extracted
# note HTML into a template's prose text-block, by replacing the hidden
# +FootnoteTexts XHTML payload the fn_* points at. The visible "[Text block
# added]" trigger cell is left untouched. V1 fills EXISTING popup-backed
# targets only (must already have an fn_*); it never fabricates a new popup.

# The mTool text-block wrapper (Windows recon 2026-07-05). Line breaks are
# Excel's "_x000D_" carriage-return token + a literal newline, matching the
# proven artifact so mTool's editor parses it identically.
_FN_BODY_STYLE = ("font-family:'Arial';font-size:12pt;"
                  "background-color:#FFFFFF;text-align:left;")
_FN_CR = "_x000D_\n"


def _xml_escape(text: str) -> str:
    # & first, then < >. Storing an HTML string as XML text content: the reader
    # unescapes exactly one level, so an existing HTML entity (&amp;) correctly
    # survives as &amp;amp; on disk and reads back as &amp; — do NOT special-case.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_footnote_html(fragment: str) -> str:
    """Wrap an HTML fragment in mTool's TX27 XHTML text-block shell."""
    return (
        '<?xml version="1.0" ?>' + _FN_CR
        + '<html xmlns="http://www.w3.org/1999/xhtml">' + _FN_CR
        + "<head>" + _FN_CR
        + '<meta content="TX27_HTM 27.0.700.500" name="GENERATOR" />' + _FN_CR
        + "<title></title>" + _FN_CR
        + "</head>" + _FN_CR
        + '<body style="' + _FN_BODY_STYLE + '">' + _FN_CR
        + fragment + _FN_CR
        + "</body>" + _FN_CR
        + "</html>"
    )


_SI_PATTERN = re.compile(r"<si\b[^>]*>.*?</si>|<si\b[^>]*/>", re.DOTALL)


def replace_shared_string(sst_xml: str, index: int, raw_text: str) -> str:
    """Replace the ``index``-th ``<si>`` in sharedStrings.xml in place. Safe
    for note payloads: each payload is a unique blob referenced by one cell."""
    matches = list(_SI_PATTERN.finditer(sst_xml))
    if not 0 <= index < len(matches):
        raise ValueError(f"shared-string index {index} out of range "
                         f"({len(matches)} entries)")
    m = matches[index]
    new_si = ('<si><t xml:space="preserve">' + _xml_escape(raw_text)
              + "</t></si>")
    return sst_xml[:m.start()] + new_si + sst_xml[m.end():]


def _bump_sst_counts(sst_xml: str, delta: int) -> str:
    m = re.search(r"<sst\b[^>]*>", sst_xml)
    if not m:
        return sst_xml
    tag = re.sub(r'\b(count|uniqueCount)="(\d+)"',
                 lambda a: f'{a.group(1)}="{int(a.group(2)) + delta}"',
                 m.group(0))
    return sst_xml[:m.start()] + tag + sst_xml[m.end():]


def append_shared_string(sst_xml: str, raw_text: str):
    """Append a new ``<si>`` before ``</sst>`` and bump count/uniqueCount.
    Returns (new_xml, new_index). Used when the payload cell is empty."""
    matches = list(_SI_PATTERN.finditer(sst_xml))
    new_index = len(matches)
    new_si = ('<si><t xml:space="preserve">' + _xml_escape(raw_text)
              + "</t></si>")
    close = re.search(r"</sst>", sst_xml)
    if not close:
        raise ValueError("no </sst> in sharedStrings.xml")
    out = sst_xml[:close.start()] + new_si + sst_xml[close.start():]
    return _bump_sst_counts(out, +1), new_index


def _cell_shared_index(sheet_xml: str, addr: str):
    """The shared-string index a ``t="s"`` cell points at, or None."""
    m = _cell_pattern(addr).search(sheet_xml)
    if not m:
        return None
    cell = m.group(0)
    opening = re.match(r"<c\b[^>]*?/?>", cell).group(0)
    if _attr(opening, "t") != "s":
        return None
    v = re.search(r"<v\b[^>]*>(.*?)</v>", cell, re.DOTALL)
    if not v:
        return None
    try:
        return int(v.group(1))
    except ValueError:
        return None


def _rebuild_shared_cell(addr: str, style, index: int) -> str:
    s_part = f' s="{style}"' if style is not None else ""
    return f'<c r="{addr}"{s_part} t="s"><v>{index}</v></c>'


def patch_shared_cell(sheet_xml: str, addr: str, index: int):
    """Point cell ``addr`` at shared-string ``index`` (``t="s"``), preserving
    style. Mirrors patch_cell_in_sheet's surgery but writes a shared cell."""
    if "<sheetData" not in sheet_xml:
        if re.search(r"<\w+:sheetData\b", sheet_xml):
            raise PrefixedSheetError(
                "sheet XML uses a namespace prefix; refusing to patch")
        raise ValueError("no <sheetData> element found in sheet XML")
    col, row_num = split_ref(addr)
    cell_m = _cell_pattern(addr).search(sheet_xml)
    if cell_m:
        cell_xml = cell_m.group(0)
        if re.search(r"<f[ >/]", cell_xml):
            raise ValueError(f"refusing to overwrite formula cell {addr}")
        opening = re.match(r"<c\b[^>]*?/?>", cell_xml).group(0)
        style = _attr(opening, "s")
        new_cell = _rebuild_shared_cell(addr, style, index)
        return sheet_xml[:cell_m.start()] + new_cell + sheet_xml[cell_m.end():]
    new_cell = _rebuild_shared_cell(addr, None, index)
    row_m = _row_pattern(row_num).search(sheet_xml)
    if not row_m:
        raise ValueError(f"no row {row_num} for footnote payload cell {addr}")
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
    return sheet_xml[:row_m.start()] + new_row + sheet_xml[row_m.end():]


def _norm_ws(text) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _footnote_label_core(text: str) -> str:
    """Normalize a visible-row label and strip mTool taxonomy decoration so
    'Property, plant and equipment' matches '*Disclosure of property, plant
    and equipment [text block]'."""
    core = normalize_label(text)
    core = re.sub(r"^\*+\s*", "", core)
    core = re.sub(r"^disclosure of\s+", "", core)
    core = re.sub(r"\s*\[text\s*block\]$", "", core)
    return core.strip()


def resolve_footnote_by_label(label: str, targets: list) -> dict:
    """Find the fn_* whose visible-row text matches ``label``. Reuses the
    numeric path's fuzzy posture but is containment-aware (mTool labels wrap
    the concept in 'Disclosure of … [text block]'). Returns
    {status: resolved|ambiguous|unresolved, key?, matched_label?, ratio?}."""
    want = _footnote_label_core(label)
    scored = []
    for t in targets:
        best = 0.0
        best_label = ""
        for txt in t["row_text"].values():
            core = _footnote_label_core(txt)
            if not core:
                continue
            if want == core:
                ratio = 1.0
            elif want and (want in core or core in want):
                ratio = 0.95
            else:
                ratio = difflib.SequenceMatcher(None, want, core).ratio()
            if ratio > best:
                best, best_label = ratio, txt
        scored.append((best, t["key"], best_label))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < FUZZY_THRESHOLD:
        return {"status": "unresolved",
                "detail": f"no fn_* label matched {label!r}"}
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        tied = [k for r, k, _ in scored if r == scored[0][0]]
        return {"status": "ambiguous",
                "detail": f"label matches multiple fn_*: {tied}"}
    return {"status": "resolved", "key": scored[0][1],
            "matched_label": scored[0][2], "ratio": round(scored[0][0], 3)}


def validate_notes_input(doc: dict) -> list:
    errors = []
    items = doc.get("footnotes")
    if not isinstance(doc, dict) or not isinstance(items, list) or not items:
        return ["input must be an object with a non-empty 'footnotes' list"]
    for i, it in enumerate(items):
        where = f"footnotes[{i}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        has_key = bool(it.get("key"))
        has_label = bool(it.get("label"))
        has_cell = bool(it.get("sheet")) and bool(it.get("cell"))
        if not (has_key or has_label or has_cell):
            errors.append(
                f"{where}: need 'key', 'label', or both 'sheet' and 'cell'")
        if not isinstance(it.get("html"), str) or not it["html"].strip():
            errors.append(f"{where}: 'html' must be a non-empty string")
    return errors


def fill_footnotes(workbook_path: str, doc: dict, output_path: str | None = None,
                   *, dry_run: bool = False) -> dict:
    """Fill prose-note text-blocks from ``doc``'s ``footnotes`` list. Assumes
    ``doc`` passed :func:`validate_notes_input`. Mirrors :func:`fill_workbook`
    (one patcher, no fork). Targets EXISTING popup-backed notes only."""
    _, data, _ = load_workbook_entries(workbook_path)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    defined = get_defined_names(data, "fn_")
    footnote_sheet = find_footnote_sheet(sheet_paths)

    report = {
        "workbook": workbook_path,
        "output": None if dry_run else output_path,
        "dry_run": bool(dry_run),
        "footnotes_written": [], "unresolved": [], "errors": [],
        "footnote_mismatches": [],
    }
    fn_entry = sheet_paths.get(footnote_sheet) if footnote_sheet else None
    if fn_entry is None or "xl/sharedStrings.xml" not in data:
        report["errors"].append(
            {"error": "workbook has no +FootnoteTexts sheet / sharedStrings.xml"})
        report["status"] = "degraded"
        return report

    cell_to_fn = {f"{d['sheet']}!{d['cell']}": k for k, d in defined.items()}
    fn_rows = read_footnote_rows(data, sheet_paths, sst, footnote_sheet)
    fn_sheet_xml = data[fn_entry].decode("utf-8")
    sst_xml = data["xl/sharedStrings.xml"].decode("utf-8")
    targets = None  # inspect_footnotes result, built lazily for label matches

    resolved_html = {}
    seen_keys = set()
    for i, it in enumerate(doc["footnotes"]):
        base = {"index": i, "sheet": it.get("sheet"), "cell": it.get("cell"),
                "key": it.get("key"), "label": it.get("label")}
        key = it.get("key")
        if not key and it.get("label"):
            if targets is None:
                targets = inspect_footnotes(data)["targets"]
            res = resolve_footnote_by_label(it["label"], targets)
            if res["status"] != "resolved":
                report["unresolved"].append({**base, **res})
                continue
            key = res["key"]
            base.update(matched_label=res.get("matched_label"),
                        ratio=res.get("ratio"))
        elif not key:
            vis = f"{it['sheet']}!{str(it['cell']).upper()}"
            key = cell_to_fn.get(vis)
            if not key:
                report["unresolved"].append({**base, "detail":
                    f"no fn_* backs {vis}; V1 fills existing popups only"})
                continue
        base["key"] = key
        if key in seen_keys:
            report["errors"].append({**base, "error":
                f"duplicate footnote write to {key}"})
            continue
        fn = fn_rows.get(key)
        if fn is None:
            report["unresolved"].append({**base, "detail":
                f"no {footnote_sheet} payload row for {key}"})
            continue
        seen_keys.add(key)
        payload_addr = f"{fn['payload_col'] or 'C'}{fn['row']}"
        wrapped = wrap_footnote_html(it["html"])
        try:
            existing = _cell_shared_index(fn_sheet_xml, payload_addr)
            if existing is not None:
                sst_xml = replace_shared_string(sst_xml, existing, wrapped)
                action = "shared_string_replaced"
            else:
                sst_xml, new_index = append_shared_string(sst_xml, wrapped)
                fn_sheet_xml = patch_shared_cell(
                    fn_sheet_xml, payload_addr, new_index)
                action = "shared_string_appended"
        except (ValueError, PrefixedSheetError) as exc:
            report["errors"].append({**base, "error": str(exc)})
            continue
        base.update(hidden_sheet=footnote_sheet, hidden_cell=payload_addr,
                    action=action)
        resolved_html[key] = it["html"]
        report["footnotes_written"].append(base)

    if not dry_run:
        write_patched_zip(workbook_path, output_path, {
            "xl/sharedStrings.xml": sst_xml.encode("utf-8"),
            fn_entry: fn_sheet_xml.encode("utf-8"),
        })
        report["footnote_mismatches"] = _verify_footnotes(
            output_path, footnote_sheet, report["footnotes_written"],
            resolved_html)

    degraded = any(report[k] for k in
                   ("unresolved", "errors", "footnote_mismatches"))
    report["status"] = "degraded" if degraded else "ok"
    return report


def _verify_footnotes(path: str, footnote_sheet: str, written: list,
                      html_by_key: dict) -> list:
    """Read back each written payload; a fragment that isn't present is a
    mismatch. get_shared_strings unescapes, so the stored HTML round-trips."""
    _, data, _ = load_workbook_entries(path)
    sheet_paths = get_sheet_paths(data)
    fn_rows = read_footnote_rows(data, sheet_paths,
                                 get_shared_strings(data), footnote_sheet)
    mismatches = []
    for w in written:
        fn = fn_rows.get(w["key"])
        payload = fn["payload_text"] if fn else None
        want = _norm_ws(html_by_key.get(w["key"]))
        if not payload or (want and want not in _norm_ws(payload)):
            mismatches.append({"key": w["key"], "found": bool(payload)})
    return mismatches


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

    report = fill_workbook(
        args.workbook, doc,
        output_path=None if args.dry_run else args.output,
        strict=bool(getattr(args, "strict", False)),
        force_recalc=bool(args.force_recalc),
        dry_run=bool(args.dry_run),
    )

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    _print_summary(report)
    degraded = report["status"] == "degraded"
    return 1 if degraded else 0


def fill_workbook(
    workbook_path: str,
    doc: dict,
    output_path: str | None = None,
    *,
    strict: bool = False,
    force_recalc: bool = False,
    dry_run: bool = False,
) -> dict:
    """Patch ``workbook_path`` with ``doc``'s writes; return the run report.

    The shared core of the CLI ``fill`` command and the server-side patch
    endpoint — one patcher, no fork (docs/PLAN.md invariant). Assumes ``doc``
    already passed :func:`validate_input`. When ``dry_run`` is False an
    ``output_path`` is required; the patched zip is written there and every
    write is read-back-verified.
    """
    _, data, _ = load_workbook_entries(workbook_path)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    sheets_cfg = doc.get("sheets", {})

    report = {
        "workbook": workbook_path,
        "output": None if dry_run else output_path,
        "dry_run": bool(dry_run),
        "written": [], "fuzzy_matched": [], "skipped_formula": [],
        "type_changed": [],
        "unresolved": [], "ambiguous": [], "mismatches": [], "errors": [],
        "force_recalc": None,
    }

    # Strict mode refuses fuzzy matches (writes them to `unresolved` instead of
    # applying). CLI --strict OR a doc-level "strict": true (the exporter sets
    # this on machine-generated docs, where a non-exact label is a bug, not a
    # typo to forgive). Hand-authored operator runs default lenient.
    strict = bool(strict or doc.get("strict"))
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

    if force_recalc:
        wb_xml, found = set_full_calc_on_load(
            data["xl/workbook.xml"].decode("utf-8"))
        report["force_recalc"] = {"requested": True, "calcPr_found": found}
        if found:
            patched_xml["xl/workbook.xml"] = wb_xml
        else:
            report["errors"].append(
                {"error": "no <calcPr> in workbook.xml; fullCalcOnLoad "
                          "not set"})

    if not dry_run:
        replacements = {p: x.encode("utf-8") for p, x in patched_xml.items()}
        write_patched_zip(workbook_path, output_path, replacements)
        report["mismatches"] = verify_values(output_path, verify_targets)

    degraded = any(report[k] for k in
                   ("skipped_formula", "type_changed", "unresolved",
                    "ambiguous", "mismatches", "errors"))
    report["status"] = "degraded" if degraded else "ok"
    return report


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


def run_fill_notes(args) -> int:
    """Fill prose-note text-blocks from a JSON input (footnotes list)."""
    with open(args.input, encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    errors = validate_notes_input(doc)
    if errors:
        for e in errors:
            print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    report = fill_footnotes(
        args.workbook, doc,
        output_path=None if args.dry_run else args.output,
        dry_run=bool(args.dry_run))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"status: {report['status']}"
          + (" (dry run)" if report["dry_run"] else ""))
    print(f"  footnotes_written: {len(report['footnotes_written'])}")
    for key in ("unresolved", "footnote_mismatches", "errors"):
        entries = report[key]
        print(f"  {key + ':':<19} {len(entries)}")
        for e in entries:
            print(f"    - {e.get('key') or e.get('cell') or ''} "
                  f"{e.get('detail') or e.get('error') or e.get('found', '')}")
    return 1 if report["status"] == "degraded" else 0


def run_footnotes(args) -> int:
    """Dump every prose-note text-block target in a workbook, programmatically.

    Answers, from the file alone: how many fn_* note targets exist (coverage),
    where each visible-row label lives (so the canonical-note label matcher can
    be configured), and which payloads are already backed vs empty/missing.
    """
    _, data, _ = load_workbook_entries(args.workbook)
    info = inspect_footnotes(data)
    if getattr(args, "json", False):
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0

    targets = info["targets"]
    if not targets:
        print("no fn_* note targets found (this template has no text-block "
              "defined names — a fresh export may need text blocks added in "
              "mTool first)")
        return 0
    populated = sum(1 for t in targets if t["payload_populated"])
    empty = sum(1 for t in targets if t["has_payload_row"]
                and not t["payload_populated"])
    no_row = sum(1 for t in targets if not t["has_payload_row"])
    print(f"footnote sheet: {info['footnote_sheet']}")
    print(f"{len(targets)} fn_* note target(s) | {populated} payload-populated "
          f"| {empty} payload-empty | {no_row} no-payload-row")
    print("  key    | visible cell            | payload      | visible-row text")
    for t in targets:
        loc = f"{t['sheet']}!{t['cell']}"
        if not t["has_payload_row"]:
            payload = "MISSING"
        elif t["payload_populated"]:
            payload = f"{t['payload_col']}{t['payload_row']} [{t['payload_len']}c]"
        else:
            payload = f"{t['payload_col']}{t['payload_row']} [empty]"
        row_text = "  ".join(f"{col}={txt[:40]!r}"
                             for col, txt in t["row_text"].items()) or "(none)"
        print(f"  {t['key']:<6} | {loc:<23} | {payload:<12} | {row_text}")
    if info["orphan_payload_keys"]:
        print(f"orphan +FootnoteTexts rows (no defined name): "
              f"{info['orphan_payload_keys']}")
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

    p_fn = sub.add_parser(
        "footnotes",
        help="dump prose-note text-block targets (fn_* -> +FootnoteTexts)")
    p_fn.add_argument("--workbook", required=True)
    p_fn.add_argument("--json", action="store_true",
                      help="emit the full target map as JSON")

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

    p_notes = sub.add_parser(
        "fill-notes",
        help="fill prose-note text-blocks (HTML -> +FootnoteTexts payload)")
    p_notes.add_argument("--workbook", required=True)
    p_notes.add_argument("--input", required=True)
    p_notes.add_argument("--output")
    p_notes.add_argument("--report")
    p_notes.add_argument("--dry-run", action="store_true", dest="dry_run")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return run_inspect(args)
    if args.command == "footnotes":
        return run_footnotes(args)
    if not args.dry_run and not args.output:
        parser.error(f"{args.command} requires --output (or --dry-run)")
    if args.output and args.output == args.workbook:
        parser.error("--output must differ from --workbook "
                     "(never patch the original in place)")
    if args.command == "fill-notes":
        return run_fill_notes(args)
    return run_fill(args)


if __name__ == "__main__":
    sys.exit(main())
