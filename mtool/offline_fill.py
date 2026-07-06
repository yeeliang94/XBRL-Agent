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


def idx_to_col(idx: int) -> str:
    """Inverse of :func:`col_to_idx` (1 -> 'A', 27 -> 'AA')."""
    if idx < 1:
        raise ValueError(f"column index must be >= 1, got {idx}")
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


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
            # NB: flag_bits is deliberately NOT copied — zipfile.writestr
            # recomputes it from the payload/name (it auto-sets bit 11 = UTF-8
            # only when the name needs it), so an assignment here is overwritten.
            # mTool part names are ASCII, so this reproduces them faithfully.
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


# Characters that are ILLEGAL in XML 1.0 even when numeric-escaped: the C0
# control range except tab/LF/CR, plus the two non-characters U+FFFE/U+FFFF. A
# PDF-extracted note can carry one (a stray vertical-tab, form-feed, NUL, etc.);
# left in a shared string it makes xl/sharedStrings.xml unreadable, so Excel/
# mTool "repair or remove the unreadable content" on open — the reported
# "String properties from /xl/sharedStrings.xml" repair. XML has no legal way to
# represent them, so the only fix is to drop them. Tab/LF/CR stay.
_XML_ILLEGAL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml_escape(text: str) -> str:
    # Strip XML-illegal chars FIRST (they can't be escaped), then & < >. Storing
    # an HTML string as XML text content: the reader unescapes exactly one level,
    # so an existing HTML entity (&amp;) correctly survives as &amp;amp; on disk
    # and reads back as &amp; — do NOT special-case.
    text = _XML_ILLEGAL_RE.sub("", text)
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


# Excel's sharedStrings header carries TWO counters that track DIFFERENT things
# (ECMA-376 §18.4.9): `uniqueCount` = number of <si> entries in the table;
# `count` = total number of t="s" cell references across ALL worksheets (a
# string referenced from 3 cells counts once in uniqueCount, 3 times in count).
# Conflating them (bumping both when appending an <si>, which adds a unique
# string but NO reference) makes Excel "repair" the file — the sharedStrings
# variant of the "String properties" corruption (Windows 2026-07-06, item 14).
# So: `append_shared_string` maintains uniqueCount only, and the filler
# RECOMPUTES both absolute counts from the final workbook before writing
# (`_finalize_sst_counts`) — correct by construction across every path,
# including the created-row path that adds 3 references at once.
_TS_REF_PATTERN = re.compile(r'<c\b[^>]*?\bt="s"')


def _bump_unique_count(sst_xml: str, delta: int) -> str:
    """Bump ONLY uniqueCount (the <si> entry count). `count` (reference total)
    is not touched here — appending an <si> adds no cell reference."""
    m = re.search(r"<sst\b[^>]*>", sst_xml)
    if not m:
        return sst_xml
    tag = re.sub(r'\buniqueCount="(\d+)"',
                 lambda a: f'uniqueCount="{int(a.group(1)) + delta}"',
                 m.group(0))
    return sst_xml[:m.start()] + tag + sst_xml[m.end():]


def _set_sst_count_attrs(sst_xml: str, count: int, unique: int) -> str:
    """Write ABSOLUTE count / uniqueCount onto the <sst> tag (inserting either
    attribute if absent). Used by the filler to reconcile the header with the
    workbook's real string-reference total + <si> count at write time."""
    m = re.search(r"<sst\b[^>]*?>", sst_xml)
    if not m:
        return sst_xml
    tag = m.group(0)
    for name, val in (("count", count), ("uniqueCount", unique)):
        if re.search(rf'\b{name}="\d+"', tag):
            tag = re.sub(rf'\b{name}="\d+"', f'{name}="{val}"', tag)
        else:  # insert before the closing '>'
            tag = tag[:-1].rstrip() + f' {name}="{val}">'
    return sst_xml[:m.start()] + tag + sst_xml[m.end():]


def _finalize_sst_counts(sst_xml: str, sheet_xmls) -> str:
    """Reconcile the ``<sst>`` header with reality: ``uniqueCount`` = the ``<si>``
    total, ``count`` = every ``t="s"`` reference across the given (final)
    worksheet XMLs. Called once at write time so incremental bookkeeping across
    the append / repoint / create paths can never leave the header inconsistent
    (the Excel-repair trigger)."""
    unique = sum(1 for _ in _SI_PATTERN.finditer(sst_xml))
    refs = sum(len(_TS_REF_PATTERN.findall(xml)) for xml in sheet_xmls)
    return _set_sst_count_attrs(sst_xml, refs, unique)


def append_shared_string(sst_xml: str, raw_text: str):
    """Append a new ``<si>`` before ``</sst>`` and bump uniqueCount (NOT count —
    a bare append adds no cell reference). Returns (new_xml, new_index). The
    reference total is reconciled workbook-wide by `_finalize_sst_counts`."""
    matches = list(_SI_PATTERN.finditer(sst_xml))
    new_index = len(matches)
    new_si = ('<si><t xml:space="preserve">' + _xml_escape(raw_text)
              + "</t></si>")
    close = re.search(r"</sst>", sst_xml)
    if not close:
        raise ValueError("no </sst> in sharedStrings.xml")
    out = sst_xml[:close.start()] + new_si + sst_xml[close.start():]
    return _bump_unique_count(out, +1), new_index


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
        # Structured candidates so a UI can offer the tie as a pick-one choice
        # (each entry is an existing fn_* slot the operator may assign).
        return {"status": "ambiguous",
                "detail": f"label matches multiple fn_*: {tied}",
                "candidates": [{"key": k, "matched_label": lbl}
                               for r, k, lbl in scored
                               if r == scored[0][0]]}
    return {"status": "resolved", "key": scored[0][1],
            "matched_label": scored[0][2], "ratio": round(scored[0][0], 3)}


# A prose note whose concept has NO fn_* yet cannot be filled by the
# label->fn_* path — there is nothing to match. mTool's own layout, however,
# still carries the VISIBLE label (in a column to the LEFT of the trigger cell,
# which holds "[Text block added]"); recon showed label col D, trigger col E.
# So we can find the label in the visible sheet and CREATE a native-shaped slot
# at the trigger cell one column to its right. This is the automatic twin of the
# hand-guided fn_37 creation proven on Windows (2026-07-05): same
# _create_footnote_slot, but the visible cell is discovered from the label
# instead of being typed by an operator.
def resolve_label_to_note_cell(label: str, note_sheets: dict) -> dict:
    """Find the VISIBLE note (trigger) cell for a label that has no ``fn_*``.

    ``note_sheets`` is ``{sheet: {"label_col": str|None, "cells": {row: {col:
    (kind, text)}}}}`` for the candidate note sheets (see
    :func:`_collect_note_sheet_cells`). Matching is CONFINED to each sheet's
    single label column (``label_col``) so a heading or a duplicate of the label
    text in some OTHER column can never win — the trigger is then, by
    construction, exactly one column to the label's right (mTool's col-D-label /
    col-E-trigger layout). Matching reuses :func:`_footnote_label_core` — the
    exact posture of :func:`resolve_footnote_by_label` — so an existing-slot
    miss and a create-target hit judge the label identically. Returns
    ``{status, sheet?, cell?, label_cell?, matched_label?, ratio?, detail?}``;
    ``cell`` is the trigger cell to create the slot at. A tie across DISTINCT
    label rows is ``ambiguous`` (not created).
    """
    want = _footnote_label_core(label)
    if not want:
        return {"status": "unresolved", "detail": "empty label"}
    scored = []
    for sheet, info in note_sheets.items():
        label_col = info.get("label_col")
        if not label_col:
            continue
        trigger_col = idx_to_col(col_to_idx(label_col) + 1)
        for row_num, cols in info["cells"].items():
            cell = cols.get(label_col)
            if cell is None:
                continue
            kind, txt = cell
            if kind != "S" or not txt.strip():
                continue
            core = _footnote_label_core(txt)
            if not core:
                continue
            if want == core:
                ratio = 1.0
            elif want in core or core in want:
                ratio = 0.95
            else:
                ratio = difflib.SequenceMatcher(None, want, core).ratio()
            scored.append((ratio, sheet, label_col, trigger_col, row_num, txt))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < FUZZY_THRESHOLD:
        return {"status": "unresolved",
                "detail": f"no visible note-sheet label matched {label!r}"}
    # A tie ACROSS DISTINCT LABEL ROWS is ambiguous — creating in the wrong
    # place is worse than not creating.
    top = scored[0][0]
    tied = {(s, lc, r) for ratio, s, lc, _tc, r, _ in scored if ratio == top}
    if len(tied) > 1:
        # Structured candidates (label cell + the trigger cell a slot would be
        # created at) so a UI can offer the tie as a pick-one choice instead of
        # a dead-end refusal.
        cands, seen = [], set()
        for ratio, s, lc, tc, r, txt in scored:
            if ratio != top or (s, lc, r) in seen:
                continue
            seen.add((s, lc, r))
            cands.append({"sheet": s, "label_cell": f"{lc}{r}",
                          "cell": f"{tc}{r}", "matched_label": txt})
        cands.sort(key=lambda c: (c["sheet"], c["cell"]))
        return {"status": "ambiguous",
                "detail": f"label {label!r} matches multiple note cells: "
                          f"{sorted(f'{s}!{lc}{r}' for s, lc, r in tied)}",
                "candidates": cands}
    _, sheet, label_col, trigger_col, row_num, matched = scored[0]
    return {"status": "resolved", "sheet": sheet,
            "cell": f"{trigger_col}{row_num}",
            "label_cell": f"{label_col}{row_num}",
            "matched_label": matched, "ratio": round(top, 3)}


def _note_label_column(rows: dict, trigger_col: str | None) -> str | None:
    """The single column that holds visible note labels on a sheet. Prefer one
    column LEFT of the sheet's existing ``fn_*`` trigger column (mTool anchors
    the trigger immediately right of the label); on a sheet with no ``fn_*``
    yet, fall back to the densest shared-string column (labels are text; values
    are numbers/blank) — the same signal ``column_detect`` uses. Ties break to
    the leftmost."""
    if trigger_col:
        i = col_to_idx(trigger_col) - 1
        if i >= 1:
            return idx_to_col(i)
    counts: dict = {}
    for cols in rows.values():
        for col, (kind, txt) in cols.items():
            if kind == "S" and txt.strip():
                counts[col] = counts.get(col, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    return min((c for c, n in counts.items() if n == best), key=col_to_idx)


def _collect_note_sheet_cells(data: dict, sheet_paths: dict, sst: list,
                              footnote_sheet: str | None,
                              defined: dict) -> dict:
    """Read the candidate note sheets — the sheets a new prose slot may be
    created on — and resolve each one's label column. A sheet qualifies if its
    name starts with ``Notes`` (mTool's convention: ``Notes-CI``,
    ``Notes-Listofnotes``, …) OR already hosts an ``fn_*`` anchor. The hidden
    ``+FootnoteTexts`` payload sheet is always excluded. Returns
    ``{sheet: {"label_col": str|None, "cells": {row: {col: (kind, text)}}}}``.
    """
    fn_sheets = {d["sheet"] for d in defined.values()}
    # Most common existing trigger column per sheet (fn_* anchor columns).
    trig_cols: dict = {}
    for d in defined.values():
        try:
            col, _ = split_ref(d["cell"])
        except ValueError:
            continue
        trig_cols.setdefault(d["sheet"], {})
        trig_cols[d["sheet"]][col] = trig_cols[d["sheet"]].get(col, 0) + 1
    out: dict = {}
    for sheet, entry in sheet_paths.items():
        if sheet == footnote_sheet:
            continue
        if not (sheet.lower().startswith("notes") or sheet in fn_sheets):
            continue
        cells = read_sheet_cells(data[entry], sst)
        cols_hist = trig_cols.get(sheet)
        trigger_col = (max(cols_hist, key=lambda c: (cols_hist[c], -col_to_idx(c)))
                       if cols_hist else None)
        out[sheet] = {"label_col": _note_label_column(cells, trigger_col),
                      "cells": cells}
    return out


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


# --- slot creation (opt-in) -------------------------------------------------
# Creating a NEW popup-backed note (a concept with no fn_* at all) is the
# fragile path: mTool only renders the popup if the new artifacts match its
# native shape closely (Windows recon 2026-07-05 — a guided native-shaped
# fn_37 rendered; an under-shaped one opened empty). So we CLONE the shape from
# an existing native fn_* row rather than hardcode magic numbers, and allocate
# every id from ONE evolving state so a batch never collides (the "race").
# Off by default; the caller opts in per fill. NOT auto-triggered by a fuzzy
# label (we don't know the visible cell then) — creation needs an explicit
# visible sheet+cell target.

_TEXT_BLOCK_MARKER = "[Text block added]"


def _sheet_local_index(data: dict, sheet_name: str):
    """0-based position of ``sheet_name`` in workbook sheet order — the
    ``localSheetId`` a sheet-scoped defined name carries."""
    wb = ET.fromstring(data["xl/workbook.xml"])
    idx = 0
    for el in wb.iter():
        if _local(el.tag) == "sheet":
            if el.get("name") == sheet_name:
                return idx
            idx += 1
    return None


def _find_donor_shape(fn_rows: dict, fn_sheet_xml: str) -> dict:
    """Clone the row/payload-cell shape from an existing native fn_* row so a
    created slot looks native. Falls back to the observed defaults."""
    for key in sorted(fn_rows, key=lambda k: fn_rows[k]["row"]):
        row = fn_rows[key]["row"]
        rm = _row_pattern(row).search(fn_sheet_xml)
        if not rm:
            continue
        opentag = re.match(r"<row\b[^>]*?>", rm.group(0)).group(0)
        pstyle = None
        cm = _cell_pattern(f"C{row}").search(fn_sheet_xml)
        if cm:
            copen = re.match(r"<c\b[^>]*?/?>", cm.group(0)).group(0)
            pstyle = _attr(copen, "s")
        return {"spans": _attr(opentag, "spans"), "ht": _attr(opentag, "ht"),
                "custom_height": 'customHeight="1"' in opentag,
                "payload_style": pstyle}
    return {"spans": "1:7", "ht": "409.5", "custom_height": True,
            "payload_style": None}


def _ensure_shared_string(sst_xml: str, index_map: dict, text: str):
    """Reuse an identical existing shared string, else append one. Returns
    (sst_xml, index). ``index_map`` is updated with any append."""
    if text in index_map:
        return sst_xml, index_map[text]
    sst_xml, idx = append_shared_string(sst_xml, text)
    index_map[text] = idx
    return sst_xml, idx


def _footnote_row_xml(row_num: int, a_idx: int, b_idx: int, p_idx: int,
                      donor: dict) -> str:
    spans = f' spans="{donor["spans"]}"' if donor.get("spans") else ""
    ht = ""
    if donor.get("ht"):
        ht = f' ht="{donor["ht"]}"' + (
            ' customHeight="1"' if donor.get("custom_height") else "")
    ps = f' s="{donor["payload_style"]}"' if donor.get("payload_style") else ""
    return (f'<row r="{row_num}"{spans}{ht}>'
            f'<c r="A{row_num}" t="s"><v>{a_idx}</v></c>'
            f'<c r="B{row_num}" t="s"><v>{b_idx}</v></c>'
            f'<c r="C{row_num}"{ps} t="s"><v>{p_idx}</v></c></row>')


def _insert_row_ordered(sheet_xml: str, row_num: int, row_xml: str) -> str:
    for m in re.finditer(r'<row\b[^>]*\br="(\d+)"', sheet_xml):
        if int(m.group(1)) > row_num:
            return sheet_xml[:m.start()] + row_xml + sheet_xml[m.start():]
    close = re.search(r"</sheetData>", sheet_xml)
    if close:
        return sheet_xml[:close.start()] + row_xml + sheet_xml[close.start():]
    empty = re.search(r"<sheetData\s*/>", sheet_xml)
    if empty:
        return (sheet_xml[:empty.start()] + f"<sheetData>{row_xml}</sheetData>"
                + sheet_xml[empty.end():])
    raise ValueError("no <sheetData> to insert footnote row into")


def _bump_footnote_dimension(sheet_xml: str, row_num: int) -> str:
    m = re.search(r'<dimension ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"', sheet_xml)
    if not m or row_num <= int(m.group(4)):
        return sheet_xml
    new = f'<dimension ref="{m.group(1)}{m.group(2)}:{m.group(3)}{row_num}"'
    return sheet_xml[:m.start()] + new + sheet_xml[m.end():]


def _add_defined_name(workbook_xml: str, name: str, sheet: str, cell: str,
                      local_index) -> str:
    col, row_num = split_ref(cell)
    sheet_ref = sheet.replace("'", "''")
    ref = _xml_escape(f"'{sheet_ref}'!${col}${row_num}")
    lsid = f' localSheetId="{local_index}"' if local_index is not None else ""
    dn = f'<definedName name="{name}"{lsid}>{ref}</definedName>'
    close = re.search(r"</definedNames>", workbook_xml)
    if close:
        return workbook_xml[:close.start()] + dn + workbook_xml[close.start():]
    sheets = re.search(r"</sheets>", workbook_xml)
    if not sheets:
        raise ValueError("no </sheets> to anchor a <definedNames> block")
    return (workbook_xml[:sheets.end()] + f"<definedNames>{dn}</definedNames>"
            + workbook_xml[sheets.end():])


def _alloc_fn_key(fn_used: set) -> str:
    n = max((int(re.sub(r"\D", "", k) or 0) for k in fn_used), default=0) + 1
    key = f"fn_{n}"
    fn_used.add(key)
    return key


def _build_orphan_pool(fn_sheet_cells: dict, defined: dict,
                       fn_rows: dict) -> list:
    """The pre-provisioned, reusable ``+FootnoteTexts`` rows — an ``fn_N``
    present in column A but with NO defined name and NO payload yet. mTool
    templates ship a pool of these empty rows and assign a text block by
    reusing the next free one; the ``fn_N`` in column A is the join key mTool
    uses to link a visible cell's defined name to its payload row. Sorted
    ascending by fn number for deterministic allocation. Each entry:
    ``{key, row, payload_col, sheet}``.

    Reusing these instead of minting a fresh ``fn_N`` is the ONLY correct
    behaviour when the template pre-provisions them: a minted key that already
    exists as an orphan row creates a DUPLICATE column-A key, and mTool joins
    to the FIRST matching row (the empty one) — so the popup opens blank
    (2026-07-05 Amgen incident)."""
    pool = []
    b_idx = col_to_idx("B")
    for key in sorted(fn_rows, key=lambda k: int(re.sub(r"\D", "", k) or 0)):
        if key in defined or fn_rows[key]["payload_populated"]:
            continue
        row = fn_rows[key]["row"]
        cells = fn_sheet_cells.get(row, {})
        # Payload column read from the row itself, never assumed "C" — real
        # templates park orphan payload cells in D/F/G too.
        cand = sorted((c for c in cells if col_to_idx(c) > b_idx),
                      key=col_to_idx)
        b_cell = cells.get("B")
        pool.append({
            "key": key, "row": row,
            "payload_col": cand[0] if cand else "C",
            "sheet": b_cell[1] if b_cell and b_cell[0] == "S" else None,
        })
    return pool


def _take_orphan(pool: list, sheet: str):
    """Pop the best free orphan slot for ``sheet``: prefer one already
    pre-provisioned for that sheet (column B matches), else the lowest-numbered
    free slot. Returns None when the pool is exhausted."""
    for i, orphan in enumerate(pool):
        if orphan["sheet"] == sheet:
            return pool.pop(i)
    return pool.pop(0) if pool else None


def _create_footnote_slot(st: dict, sheet: str, cell: str, wrapped: str):
    """Wire a prose note (whose concept has no ``fn_*`` yet) to a native-shaped
    popup slot and write ``wrapped`` as its payload. Mutates ``st`` (single
    evolving allocator). Returns (fn_key, payload_addr, slot_source).

    Prefers REUSING a pre-provisioned orphan ``+FootnoteTexts`` row — that is
    how mTool itself assigns a text block, and it avoids the duplicate
    column-A key that leaves the popup empty. Only when the orphan pool is
    exhausted does it fall back to appending a brand-new native-shaped row
    with a key guaranteed unused across defined names AND existing column-A
    keys (``fn_used`` is seeded from both)."""
    vis_entry = st["sheet_paths"].get(sheet)
    if vis_entry is None:
        raise ValueError(f"visible sheet {sheet!r} not found for slot creation")

    orphan = _take_orphan(st["orphan_pool"], sheet)
    if orphan is not None:
        key = orphan["key"]
        row_num = orphan["row"]
        payload_addr = f"{orphan['payload_col']}{row_num}"
        source = "orphan_reused"
        # The orphan's payload cell is empty by construction. NEVER
        # replace_shared_string here: an empty t="s" cell points at a shared
        # "" <si> that other cells across the workbook may reference
        # (sharedStrings dedups), and replacing it would rewrite them all.
        # Append a fresh unique <si> and repoint the cell (style preserved).
        st["sst_xml"], p_idx = append_shared_string(st["sst_xml"], wrapped)
        st["fn_sheet_xml"] = patch_shared_cell(
            st["fn_sheet_xml"], payload_addr, p_idx)
        # Column B names the visible sheet; correct it only if the reused
        # orphan was pre-provisioned for a different sheet (or blank).
        if orphan["sheet"] != sheet:
            st["sst_xml"], b_idx = _ensure_shared_string(
                st["sst_xml"], st["sindex"], sheet)
            st["fn_sheet_xml"] = patch_shared_cell(
                st["fn_sheet_xml"], f"B{row_num}", b_idx)
    else:
        key = _alloc_fn_key(st["fn_used"])
        row_num = st["next_row"]
        st["next_row"] += 1
        payload_addr = f"C{row_num}"
        source = "row_appended"
        st["sst_xml"], a_idx = _ensure_shared_string(
            st["sst_xml"], st["sindex"], key)
        st["sst_xml"], b_idx = _ensure_shared_string(
            st["sst_xml"], st["sindex"], sheet)
        st["sst_xml"], p_idx = append_shared_string(st["sst_xml"], wrapped)
        row_xml = _footnote_row_xml(row_num, a_idx, b_idx, p_idx, st["donor"])
        st["fn_sheet_xml"] = _bump_footnote_dimension(
            _insert_row_ordered(st["fn_sheet_xml"], row_num, row_xml), row_num)

    # Common tail (both paths): visible trigger marker + defined name.
    st["sst_xml"], trig_idx = _ensure_shared_string(
        st["sst_xml"], st["sindex"], _TEXT_BLOCK_MARKER)
    vis_xml = st["visible"].get(vis_entry) or st["data"][vis_entry].decode("utf-8")
    st["visible"][vis_entry] = patch_shared_cell(vis_xml, cell, trig_idx)
    st["workbook_xml"] = _add_defined_name(
        st["workbook_xml"], key, sheet, cell,
        _sheet_local_index(st["data"], sheet))
    return key, payload_addr, source


def fill_footnotes(workbook_path: str, doc: dict, output_path: str | None = None,
                   *, dry_run: bool = False, create_missing: bool = False,
                   strict: bool = False) -> dict:
    """Fill prose-note text-blocks from ``doc``'s ``footnotes`` list. Assumes
    ``doc`` passed :func:`validate_notes_input`. Mirrors :func:`fill_workbook`
    (one patcher, no fork).

    Default targets EXISTING popup-backed notes only. With ``create_missing``,
    a note with no ``fn_*`` gets a new native-shaped slot created for it (opt-in,
    fragile — verify render in mTool). Two create paths:

    * an item that gives an explicit visible ``sheet``+``cell`` — the operator
      typed the trigger cell; or
    * a ``label``-targeted item whose label is found in a VISIBLE note sheet
      (:func:`resolve_label_to_note_cell`) — the slot is created at the trigger
      cell one column right of the matched label (mTool's col-D-label /
      col-E-trigger layout). This is the automatic path the notes exporter
      feeds. A label that matches no existing ``fn_*`` AND no visible note-sheet
      label stays ``unresolved`` (never guessed).

    ``strict`` (CLI ``--strict`` OR a doc-level ``"strict": true``, which the
    notes exporter sets on machine-generated docs) refuses a non-exact label
    match — a fuzzy/containment hit lands in ``unresolved`` instead of risking
    the wrong text-block (applies to both the existing-slot match and the
    create-by-label visible-label match). Mirrors :func:`fill_workbook`'s
    strict mode."""
    _, data, _ = load_workbook_entries(workbook_path)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    defined = get_defined_names(data, "fn_")
    footnote_sheet = find_footnote_sheet(sheet_paths)

    # CLI --strict OR a doc-level "strict": true (the notes exporter sets this
    # on machine-generated docs, where a non-exact label is a bug, not a typo).
    strict = bool(strict or doc.get("strict"))
    report = {
        "workbook": workbook_path,
        "output": None if dry_run else output_path,
        "dry_run": bool(dry_run), "create_missing": bool(create_missing),
        "strict": strict,
        "footnotes_written": [], "footnotes_created": [],
        "unresolved": [], "errors": [], "footnote_mismatches": [],
    }
    fn_entry = sheet_paths.get(footnote_sheet) if footnote_sheet else None
    if fn_entry is None or "xl/sharedStrings.xml" not in data:
        report["errors"].append(
            {"error": "workbook has no +FootnoteTexts sheet / sharedStrings.xml"})
        report["status"] = "degraded"
        # Still emit the output (a byte-copy) so a caller chaining this after
        # another fill always has a downloadable file, even on this no-op path.
        if not dry_run and output_path:
            write_patched_zip(workbook_path, output_path, {})
        return report

    cell_to_fn = {f"{d['sheet']}!{d['cell']}": k for k, d in defined.items()}
    fn_rows = read_footnote_rows(data, sheet_paths, sst, footnote_sheet)
    fn_sheet_cells = read_sheet_cells(data[fn_entry], sst)
    fn_sheet_xml = data[fn_entry].decode("utf-8")
    sst_xml = data["xl/sharedStrings.xml"].decode("utf-8")
    targets = None  # inspect_footnotes result, built lazily for label matches
    note_cells = None  # visible note-sheet cells, built lazily for create-by-label

    # Single evolving allocator state — every new fn#/row/sst-index is drawn
    # from here so a batch of creates can never collide (the "race").
    st = {
        "data": data, "sheet_paths": sheet_paths,
        "workbook_xml": data["xl/workbook.xml"].decode("utf-8"),
        "fn_sheet_xml": fn_sheet_xml, "sst_xml": sst_xml,
        "visible": {},  # entry_path -> patched xml (created triggers)
        "sindex": {v: i for i, v in enumerate(sst)},  # first-seen value->index
        # Seed from EVERY fn_ key — defined names AND +FootnoteTexts column-A
        # keys — so a fallback append can never mint a key that duplicates a
        # pre-provisioned orphan row (the "popup opens empty" incident).
        "fn_used": set(defined) | set(fn_rows),
        "next_row": _max_row(fn_sheet_xml) + 1,
        "donor": _find_donor_shape(fn_rows, fn_sheet_xml),
        "orphan_pool": _build_orphan_pool(fn_sheet_cells, defined, fn_rows),
    }
    report["fn_allocation"] = {
        "orphan_pool_initial": len(st["orphan_pool"]),
        "orphan_reused": 0, "row_appended": 0,
    }

    resolved_html = {}
    seen_keys = set()
    for i, it in enumerate(doc["footnotes"]):
        base = {"index": i, "sheet": it.get("sheet"), "cell": it.get("cell"),
                "key": it.get("key"), "label": it.get("label")}
        wrapped = wrap_footnote_html(it["html"])
        key = it.get("key")
        if not key and it.get("label"):
            if targets is None:
                targets = inspect_footnotes(data)["targets"]
            res = resolve_footnote_by_label(it["label"], targets)
            if res["status"] == "resolved":
                if strict and res.get("ratio", 1.0) < 1.0:
                    report["unresolved"].append({**base, **res,
                        "reason": "strict_near_miss", "detail":
                        f"strict mode: non-exact label match "
                        f"(similarity {res['ratio']}) refused; would have "
                        f"matched {res.get('matched_label')!r}"})
                    continue
                key = res["key"]
                base.update(matched_label=res.get("matched_label"),
                            ratio=res.get("ratio"))
            elif create_missing:
                # No existing fn_* backs this note. Locate the label in the
                # VISIBLE note sheets and create a native-shaped slot at the
                # trigger cell (col D label -> col E trigger). One of three
                # outcomes: create a new slot, fall through to fill an already-
                # backed trigger cell, or report unresolved.
                if note_cells is None:
                    note_cells = _collect_note_sheet_cells(
                        data, sheet_paths, sst, footnote_sheet, defined)
                cres = resolve_label_to_note_cell(it["label"], note_cells)
                if cres["status"] != "resolved":
                    # Prefer the create-path detail (visible-label miss /
                    # ambiguity) — it's the actionable one here.
                    report["unresolved"].append({**base, **cres,
                        "reason": ("ambiguous"
                                   if cres["status"] == "ambiguous"
                                   else "no_match")})
                    continue
                if strict and cres.get("ratio", 1.0) < 1.0:
                    report["unresolved"].append({**base, **cres,
                        "reason": "strict_near_miss",
                        "detail": "strict mode: non-exact visible-label match "
                        f"(similarity {cres['ratio']}) refused; would have "
                        f"created at {cres['sheet']}!{cres['cell']} from "
                        f"{cres['matched_label']!r}"})
                    continue
                c_sheet, c_cell = cres["sheet"], cres["cell"]
                key = cell_to_fn.get(f"{c_sheet}!{c_cell}")
                if key:
                    # The trigger cell already has an fn_* (its label just
                    # didn't match) — fill it, don't duplicate; fall through.
                    base.update(matched_label=cres["matched_label"],
                                ratio=cres["ratio"], resolved_via="label->cell")
                else:
                    try:
                        key, payload_addr, slot_source = _create_footnote_slot(
                            st, c_sheet, c_cell, wrapped)
                    except (ValueError, PrefixedSheetError) as exc:
                        report["errors"].append({**base, "error": str(exc)})
                        continue
                    report["fn_allocation"][slot_source] = (
                        report["fn_allocation"].get(slot_source, 0) + 1)
                    base.update(key=key, action="slot_created",
                                slot_source=slot_source,
                                resolved_via="label->cell",
                                visible_cell=f"{c_sheet}!{c_cell}",
                                label_cell=cres["label_cell"],
                                matched_label=cres["matched_label"],
                                ratio=cres["ratio"], hidden_sheet=footnote_sheet,
                                hidden_cell=payload_addr)
                    # Register the new slot so a later item resolving to the
                    # SAME visible cell (a duplicate label) hits the seen_keys
                    # duplicate guard instead of minting a second fn_* here.
                    cell_to_fn[f"{c_sheet}!{c_cell}"] = key
                    seen_keys.add(key)
                    resolved_html[key] = it["html"]
                    report["footnotes_created"].append(base)
                    report["footnotes_written"].append(base)
                    continue
            else:
                report["unresolved"].append({**base, **res,
                    "reason": ("ambiguous" if res["status"] == "ambiguous"
                               else "no_match")})
                continue
        elif not key:
            cell = str(it["cell"]).upper()
            vis = f"{it['sheet']}!{cell}"
            key = cell_to_fn.get(vis)
            if not key and create_missing:
                # No fn_* here: build a native-shaped slot at this visible cell.
                try:
                    key, payload_addr, slot_source = _create_footnote_slot(
                        st, it["sheet"], cell, wrapped)
                except (ValueError, PrefixedSheetError) as exc:
                    report["errors"].append({**base, "error": str(exc)})
                    continue
                report["fn_allocation"][slot_source] = (
                    report["fn_allocation"].get(slot_source, 0) + 1)
                base.update(key=key, hidden_sheet=footnote_sheet,
                            hidden_cell=payload_addr, action="slot_created",
                            slot_source=slot_source)
                # Register the new slot so a later explicit item for the SAME
                # cell hits the seen_keys duplicate guard, not a second create.
                cell_to_fn[vis] = key
                seen_keys.add(key)
                resolved_html[key] = it["html"]
                report["footnotes_created"].append(base)
                report["footnotes_written"].append(base)
                continue
            if not key:
                report["unresolved"].append({**base, "reason": "no_slot",
                    "detail":
                    f"no fn_* backs {vis}; pass create_missing to create it"})
                continue
        base["key"] = key
        if key in seen_keys:
            report["errors"].append({**base, "error":
                f"duplicate footnote write to {key}"})
            continue
        fn = fn_rows.get(key)
        if fn is None:
            report["unresolved"].append({**base, "reason": "no_payload_row",
                "detail": f"no {footnote_sheet} payload row for {key}"})
            continue
        seen_keys.add(key)
        payload_addr = f"{fn['payload_col'] or 'C'}{fn['row']}"
        try:
            existing = _cell_shared_index(st["fn_sheet_xml"], payload_addr)
            # Replace in place ONLY for a non-empty payload <si> — a unique
            # blob per the mTool convention. An EMPTY t="s" cell points at a
            # shared "" <si> that sharedStrings dedup may bind to other cells
            # workbook-wide; replacing it would rewrite them all. Append+patch
            # is always safe for that case.
            if existing is not None and (existing >= len(sst)
                                         or sst[existing].strip()):
                st["sst_xml"] = replace_shared_string(
                    st["sst_xml"], existing, wrapped)
                action = "shared_string_replaced"
            else:
                st["sst_xml"], new_index = append_shared_string(
                    st["sst_xml"], wrapped)
                st["fn_sheet_xml"] = patch_shared_cell(
                    st["fn_sheet_xml"], payload_addr, new_index)
                action = "shared_string_appended"
        except (ValueError, PrefixedSheetError) as exc:
            report["errors"].append({**base, "error": str(exc)})
            continue
        base.update(hidden_sheet=footnote_sheet, hidden_cell=payload_addr,
                    action=action)
        resolved_html[key] = it["html"]
        report["footnotes_written"].append(base)

    if not dry_run:
        # Reconcile the sharedStrings header with the workbook's real string
        # references BEFORE writing, using the SAME final XML that gets written
        # (modified visible sheets + fn sheet overlaid on the originals). This
        # makes count/uniqueCount correct regardless of which fill path ran —
        # the append/repoint/create bookkeeping can't drift the header.
        final_sheets = []
        for entry in set(sheet_paths.values()):
            if entry in st["visible"]:
                final_sheets.append(st["visible"][entry])
            elif entry == fn_entry:
                final_sheets.append(st["fn_sheet_xml"])
            else:
                final_sheets.append(st["data"][entry].decode("utf-8"))
        st["sst_xml"] = _finalize_sst_counts(st["sst_xml"], final_sheets)

        replacements = {
            "xl/sharedStrings.xml": st["sst_xml"].encode("utf-8"),
            fn_entry: st["fn_sheet_xml"].encode("utf-8"),
        }
        if report["footnotes_created"]:
            replacements["xl/workbook.xml"] = st["workbook_xml"].encode("utf-8")
            for entry, xml in st["visible"].items():
                replacements[entry] = xml.encode("utf-8")
        write_patched_zip(workbook_path, output_path, replacements)
        report["footnote_mismatches"] = _verify_footnotes(
            output_path, footnote_sheet, report["footnotes_written"],
            resolved_html)
        # Read-back invariant: no duplicate column-A join keys. mTool joins on
        # the FIRST matching row, so a duplicate silently strands the payload
        # (the popup opens empty) while the file stays structurally valid —
        # fail loudly here instead of silently inside mTool.
        for dup in _detect_duplicate_fn_keys(output_path, footnote_sheet):
            report["errors"].append({
                "key": dup["key"], "rows": dup["rows"],
                "error": f"duplicate +FootnoteTexts column-A key "
                         f"{dup['key']} on rows {dup['rows']}; mTool reads "
                         f"the FIRST match, so its popup would open empty"})

    degraded = any(report[k] for k in
                   ("unresolved", "errors", "footnote_mismatches"))
    report["status"] = "degraded" if degraded else "ok"
    return report


def _max_row(sheet_xml: str) -> int:
    rows = [int(m.group(1))
            for m in re.finditer(r'<row\b[^>]*\br="(\d+)"', sheet_xml)]
    return max(rows) if rows else 0


def _detect_duplicate_fn_keys(path: str, footnote_sheet: str) -> list:
    """``fn_*`` keys that appear on MORE THAN ONE ``+FootnoteTexts`` row.
    mTool joins on the column-A key and reads the FIRST match, so a duplicate
    silently strands the payload (2026-07-05 Amgen incident) — this is a
    correctness invariant, not a warning. Deliberately a RAW row scan:
    :func:`read_footnote_rows` keys by column A and keeps the LAST row, which
    is exactly the masking that let the incident pass read-back verification.
    Returns ``[{key, rows}, ...]`` sorted by key."""
    _, data, _ = load_workbook_entries(path)
    sheet_paths = get_sheet_paths(data)
    entry = sheet_paths.get(footnote_sheet) if footnote_sheet else None
    if entry is None:
        return []
    cells = read_sheet_cells(data[entry], get_shared_strings(data))
    rows_by_key: dict = {}
    for row_num in sorted(cells):
        a = cells[row_num].get("A")
        if a and a[0] == "S" and a[1].strip().startswith("fn_"):
            rows_by_key.setdefault(a[1].strip(), []).append(row_num)
    return [{"key": k, "rows": rows} for k, rows in sorted(rows_by_key.items())
            if len(rows) > 1]


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
        dry_run=bool(args.dry_run),
        create_missing=bool(getattr(args, "create_missing", False)))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"status: {report['status']}"
          + (" (dry run)" if report["dry_run"] else ""))
    print(f"  footnotes_written: {len(report['footnotes_written'])}"
          + (f" ({len(report['footnotes_created'])} slot(s) created)"
             if report.get("footnotes_created") else ""))
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
    p_notes.add_argument(
        "--create-missing", action="store_true", dest="create_missing",
        help="create a native-shaped popup slot for a footnote item whose "
             "explicit sheet+cell has no fn_* (opt-in; verify render in mTool)")

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
