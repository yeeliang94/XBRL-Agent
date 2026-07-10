#!/usr/bin/env python3
r"""Diagnose mTool note workbooks and build disposable Excel-boundary probes.

This is an investigation tool for the Windows box, not part of the production
fill path.  It answers the questions that a successful ``zipfile`` open cannot:

* Is every XML part well formed?
* Do shared-string header counts match the worksheet references?
* Does any worksheet point outside the shared-string table?
* Are ``fn_*`` join keys duplicated?
* What are the stored and Excel-decoded lengths of every populated note?
* Is the decoded XHTML still complete and parseable?

The ``make-boundary`` command deliberately bypasses the production 32,767-char
guard so a throwaway workbook can test Excel at 32,766 / 32,767 / 32,768 (or
another requested boundary).  It is gated by an explicit acknowledgement,
requires a different output path, refuses shared payload strings, and never
changes the source workbook.

Examples (Windows, from the repository root)::

    set PYTHONUTF8=1
    py -3 mtool\examples\mtool_broken_file_probe.py inspect ^
      --workbook filled.xlsx --json-out filled.inspect.json

    py -3 mtool\examples\mtool_broken_file_probe.py make-boundary ^
      --workbook dummy.xlsx --output probe-32768.xlsx --key fn_14 ^
      --decoded-length 32768 --unsafe-boundary-probe

    py -3 mtool\examples\mtool_broken_file_probe.py compare ^
      --before compact-before.xlsx --after compact-after-mtool-save.xlsx ^
      --key fn_14 --json-out compact-roundtrip.json

Dummy/test filings only.  Never use ``make-boundary`` on a real filing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Optional, Set, Union
import xml.etree.ElementTree as ET
import zipfile

# Allow ``python mtool/examples/mtool_broken_file_probe.py`` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mtool.offline_fill import (  # noqa: E402
    EXCEL_CELL_CHAR_LIMIT,
    _cell_shared_index,
    _detect_duplicate_fn_keys,
    find_footnote_sheet,
    get_shared_strings,
    get_sheet_paths,
    load_workbook_entries,
    read_footnote_rows,
    replace_shared_string,
    wrap_footnote_html,
    write_patched_zip,
)


_XSTRING_ESCAPE_RE = re.compile(r"_x([0-9a-fA-F]{4})_")


def decode_excel_xstring(value: str) -> str:
    """Decode the ``_xHHHH_`` escapes used by Excel Xstrings.

    ``_x005F_`` immediately before another escape protects that next sequence,
    so ``_x005F__x000D_`` becomes the literal text ``_x000D_`` rather than a
    carriage return.  That distinction matters when calculating the limit.
    """
    out: list[str] = []
    i = 0
    while i < len(value):
        match = _XSTRING_ESCAPE_RE.match(value, i)
        if match is None:
            out.append(value[i])
            i += 1
            continue
        code = int(match.group(1), 16)
        if code == 0x005F:
            protected = _XSTRING_ESCAPE_RE.match(value, match.end())
            if protected is not None:
                out.append(protected.group(0))
                i = protected.end()
                continue
        out.append(chr(code))
        i = match.end()
    return "".join(out)


def utf16_units(value: str) -> int:
    """Number of UTF-16 code units (the safest Excel character proxy)."""
    return len(value.encode("utf-16-le")) // 2


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def payload_metrics(stored: str) -> dict:
    """Return stored/decoded lengths, hashes and XHTML validity for one note."""
    decoded = decode_excel_xstring(stored)
    xhtml = decoded
    xml_start = xhtml.find("<?xml")
    if xml_start > 0 and xhtml[:xml_start].strip() == "ABC":
        xhtml = xhtml[xml_start:]
    xhtml_error = None
    try:
        ET.fromstring(xhtml)
    except ET.ParseError as exc:
        xhtml_error = str(exc)
    return {
        "stored_chars": len(stored),
        "decoded_codepoints": len(decoded),
        "decoded_utf16_units": utf16_units(decoded),
        "stored_over_excel_limit": len(stored) > EXCEL_CELL_CHAR_LIMIT,
        "decoded_over_excel_limit": (
            utf16_units(decoded) > EXCEL_CELL_CHAR_LIMIT
        ),
        "xstring_escapes": len(_XSTRING_ESCAPE_RE.findall(stored)),
        "x000d_escapes": len(re.findall(r"_x000D_", stored, re.IGNORECASE)),
        "literal_newlines": stored.count("\n"),
        "stored_sha256": _sha256_text(stored),
        "decoded_sha256": _sha256_text(decoded),
        "xhtml_valid": xhtml_error is None,
        "xhtml_error": xhtml_error,
    }


def _issue(code: str, severity: str, detail: str, **context) -> dict:
    return {
        "code": code,
        "severity": severity,
        "detail": detail,
        **context,
    }


def _xml_part_issues(data: dict[str, bytes]) -> list[dict]:
    issues: list[dict] = []
    for name, payload in sorted(data.items()):
        if not (name.endswith(".xml") or name.endswith(".rels")
                or name == "[Content_Types].xml"):
            continue
        try:
            ET.fromstring(payload)
        except ET.ParseError as exc:
            issues.append(_issue(
                "invalid_xml_part", "error", f"{name}: {exc}", part=name,
            ))
    return issues


def _sst_header(data: dict[str, bytes]) -> tuple[dict, list[dict]]:
    issues: list[dict] = []
    raw = data.get("xl/sharedStrings.xml")
    if raw is None:
        return {"present": False, "si_count": 0}, issues
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {"present": True, "parseable": False}, issues

    def _int_attr(name: str):
        value = root.get(name)
        try:
            return int(value) if value is not None else None
        except ValueError:
            issues.append(_issue(
                "invalid_shared_strings_header", "error",
                f"sharedStrings {name} is not an integer: {value!r}",
            ))
            return None

    si_count = sum(1 for child in root if child.tag.rsplit("}", 1)[-1] == "si")
    return {
        "present": True,
        "parseable": True,
        "count_attr": _int_attr("count"),
        "unique_count_attr": _int_attr("uniqueCount"),
        "si_count": si_count,
    }, issues


def _shared_string_refs(data: dict[str, bytes]) -> tuple[list[dict], list[dict]]:
    refs: list[dict] = []
    issues: list[dict] = []
    for name, payload in sorted(data.items()):
        if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
            continue
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            # _xml_part_issues owns the canonical invalid-XML finding.
            continue
        for cell in root.iter():
            if cell.tag.rsplit("}", 1)[-1] != "c" or cell.get("t") != "s":
                continue
            value = next(
                (child for child in cell
                 if child.tag.rsplit("}", 1)[-1] == "v"),
                None,
            )
            try:
                index = int(value.text) if value is not None else None
            except (TypeError, ValueError):
                index = None
            if index is None:
                issues.append(_issue(
                    "invalid_shared_string_ref", "error",
                    f"{name} contains a t='s' cell without an integer <v>",
                    part=name, cell=cell.get("r"),
                ))
                continue
            refs.append({"part": name, "cell": cell.get("r"), "index": index})
    return refs, issues


def inspect_workbook(
    path: Union[str, Path], keys: Optional[Set[str]] = None,
) -> dict:
    """Read-only static inspection.  It reports candidates, not an Excel verdict."""
    workbook = Path(path).resolve()
    result = {
        "schema": "mtool-broken-file-probe/v1",
        "workbook": str(workbook),
        "workbook_bytes": workbook.stat().st_size if workbook.exists() else None,
        "workbook_sha256": None,
        "status": "error",
        "issues": [],
        "zip": {},
        "shared_strings": {},
        "footnote_sheet": None,
        "duplicate_fn_keys": [],
        "payloads": [],
    }
    if not workbook.is_file():
        result["issues"].append(_issue(
            "file_not_found", "error", f"workbook not found: {workbook}",
        ))
        return result

    raw_file = workbook.read_bytes()
    result["workbook_sha256"] = _sha256_bytes(raw_file)
    try:
        with zipfile.ZipFile(workbook) as zf:
            bad_member = zf.testzip()
            names = zf.namelist()
            duplicates = [name for name, count in Counter(names).items() if count > 1]
            result["zip"] = {
                "members": len(names),
                "crc_error_member": bad_member,
                "duplicate_members": sorted(duplicates),
                "member_names": sorted(set(names)),
            }
            if bad_member:
                result["issues"].append(_issue(
                    "zip_crc_error", "error",
                    f"ZIP CRC check failed at {bad_member}", part=bad_member,
                ))
            if duplicates:
                result["issues"].append(_issue(
                    "duplicate_zip_members", "error",
                    "ZIP contains duplicate member names; readers may disagree "
                    "about which copy wins",
                    members=sorted(duplicates),
                ))
    except (zipfile.BadZipFile, OSError) as exc:
        result["issues"].append(_issue(
            "invalid_zip", "error", str(exc),
        ))
        return result

    try:
        _infos, data, _comment = load_workbook_entries(str(workbook))
    except Exception as exc:  # diagnostic boundary: preserve the root exception
        result["issues"].append(_issue(
            "package_read_failed", "error", f"{type(exc).__name__}: {exc}",
        ))
        return result

    required = {
        "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
    }
    missing = sorted(required - set(data))
    if missing:
        result["issues"].append(_issue(
            "missing_core_parts", "error", "missing required workbook parts",
            parts=missing,
        ))
    result["issues"].extend(_xml_part_issues(data))

    sst_header, header_issues = _sst_header(data)
    result["shared_strings"] = sst_header
    result["issues"].extend(header_issues)
    refs, ref_issues = _shared_string_refs(data)
    result["issues"].extend(ref_issues)
    result["shared_strings"]["worksheet_refs"] = len(refs)
    si_count = sst_header.get("si_count", 0)
    invalid_refs = [r for r in refs if r["index"] < 0 or r["index"] >= si_count]
    result["shared_strings"]["out_of_range_refs"] = invalid_refs
    if invalid_refs:
        result["issues"].append(_issue(
            "shared_string_ref_out_of_range", "error",
            f"{len(invalid_refs)} worksheet shared-string reference(s) point "
            f"outside {si_count} <si> entries",
            refs=invalid_refs[:20],
        ))
    if sst_header.get("present") and sst_header.get("parseable"):
        # count/uniqueCount are optional in SpreadsheetML. If present they must
        # be truthful; absence alone is not corruption.
        if (sst_header.get("unique_count_attr") is not None
                and sst_header.get("unique_count_attr") != si_count):
            result["issues"].append(_issue(
                "shared_strings_unique_count_mismatch", "error",
                f"uniqueCount={sst_header.get('unique_count_attr')} but the "
                f"table contains {si_count} <si> entries",
            ))
        if (sst_header.get("count_attr") is not None
                and sst_header.get("count_attr") != len(refs)):
            result["issues"].append(_issue(
                "shared_strings_count_mismatch", "error",
                f"count={sst_header.get('count_attr')} but worksheets contain "
                f"{len(refs)} shared-string references",
            ))

    if missing or any(i["code"] == "invalid_xml_part" for i in result["issues"]):
        return _finish_status(result)

    try:
        sheet_paths = get_sheet_paths(data)
        sst = get_shared_strings(data)
        footnote_sheet = find_footnote_sheet(sheet_paths)
        result["footnote_sheet"] = footnote_sheet
        if footnote_sheet is None:
            result["issues"].append(_issue(
                "footnote_sheet_missing", "warning",
                "no FootnoteTexts sheet found; note payloads could not be checked",
            ))
            return _finish_status(result)
        duplicate_keys = _detect_duplicate_fn_keys(str(workbook), footnote_sheet)
        result["duplicate_fn_keys"] = duplicate_keys
        if duplicate_keys:
            result["issues"].append(_issue(
                "duplicate_fn_keys", "error",
                "duplicate +FootnoteTexts column-A keys can make mTool read the "
                "wrong/empty payload",
                duplicates=duplicate_keys,
            ))
        rows = read_footnote_rows(data, sheet_paths, sst, footnote_sheet)
    except Exception as exc:
        result["issues"].append(_issue(
            "footnote_inspection_failed", "error",
            f"{type(exc).__name__}: {exc}",
        ))
        return _finish_status(result)

    selected = set(keys or ())
    if selected:
        absent = sorted(selected - set(rows))
        if absent:
            result["issues"].append(_issue(
                "requested_fn_keys_missing", "error",
                "requested fn_* keys are absent", keys=absent,
            ))
    for key, row in sorted(rows.items(), key=lambda item: item[1]["row"]):
        if selected and key not in selected:
            continue
        payload = row.get("payload_text") or ""
        entry = {
            "key": key,
            "row": row["row"],
            "payload_col": row.get("payload_col"),
            "hidden_cell": (
                f"{row.get('payload_col')}{row['row']}"
                if row.get("payload_col") else None
            ),
            "populated": bool(payload.strip()),
        }
        if payload.strip():
            metrics = payload_metrics(payload)
            entry.update(metrics)
            if metrics["decoded_over_excel_limit"]:
                result["issues"].append(_issue(
                    "decoded_payload_over_excel_limit", "error",
                    f"{key} decodes to {metrics['decoded_utf16_units']} UTF-16 "
                    f"units, above Excel's {EXCEL_CELL_CHAR_LIMIT} limit",
                    key=key, hidden_cell=entry["hidden_cell"],
                ))
            elif metrics["stored_over_excel_limit"]:
                result["issues"].append(_issue(
                    "stored_payload_over_limit_but_decoded_fits", "warning",
                    f"{key} stores {metrics['stored_chars']} characters but "
                    f"decodes to {metrics['decoded_utf16_units']} UTF-16 units; "
                    "Windows Excel round-trip evidence is required",
                    key=key, hidden_cell=entry["hidden_cell"],
                ))
            if not metrics["xhtml_valid"]:
                result["issues"].append(_issue(
                    "malformed_footnote_xhtml", "error",
                    f"{key} payload is not complete XML/XHTML: "
                    f"{metrics['xhtml_error']}",
                    key=key, hidden_cell=entry["hidden_cell"],
                ))
        result["payloads"].append(entry)

    return _finish_status(result)


def _finish_status(result: dict) -> dict:
    severities = {issue["severity"] for issue in result["issues"]}
    if "error" in severities:
        result["status"] = "root_cause_candidate_found"
    elif "warning" in severities:
        result["status"] = "needs_windows_verification"
    else:
        result["status"] = "static_checks_clean"
    return result


def _build_boundary_payload(decoded_length: int, line_breaks: int) -> str:
    if decoded_length <= 0:
        raise ValueError("decoded_length must be positive")
    if line_breaks < 0:
        raise ValueError("line_breaks must be non-negative")
    marker = "BOUNDARY-PROBE|"
    breaks = "_x000D_\n" * line_breaks
    base = wrap_footnote_html(f"<p>{marker}{breaks}</p>")
    padding = decoded_length - utf16_units(decode_excel_xstring(base))
    if padding < 0:
        raise ValueError(
            f"decoded_length {decoded_length} is too small for the XHTML shell "
            f"and {line_breaks} line break(s)"
        )
    payload = wrap_footnote_html(f"<p>{marker}{breaks}{'A' * padding}</p>")
    actual = utf16_units(decode_excel_xstring(payload))
    if actual != decoded_length:
        raise AssertionError(f"boundary payload is {actual}, expected {decoded_length}")
    return payload


def _write_payload_copy(
    workbook_path: Union[str, Path],
    output_path: Union[str, Path],
    key: str,
    payload: str,
) -> dict:
    """Replace one unique populated payload in a new copy; never in place."""
    source = Path(workbook_path).resolve()
    output = Path(output_path).resolve()
    if source == output:
        raise ValueError("refusing in-place probe write")
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output: {output}")

    _infos, data, _comment = load_workbook_entries(str(source))
    sheet_paths = get_sheet_paths(data)
    footnote_sheet = find_footnote_sheet(sheet_paths)
    if footnote_sheet is None:
        raise ValueError("no FootnoteTexts sheet found")
    duplicates = _detect_duplicate_fn_keys(str(source), footnote_sheet)
    if any(d["key"] == key for d in duplicates):
        raise ValueError(f"refusing ambiguous duplicate key {key}")
    sst = get_shared_strings(data)
    rows = read_footnote_rows(data, sheet_paths, sst, footnote_sheet)
    row = rows.get(key)
    if row is None:
        raise ValueError(f"footnote key {key!r} not found")
    payload_col = row.get("payload_col")
    if not payload_col:
        raise ValueError(f"footnote key {key!r} has no populated shared payload cell")
    hidden_cell = f"{payload_col}{row['row']}"
    fn_entry = sheet_paths[footnote_sheet]
    fn_xml = data[fn_entry].decode("utf-8")
    sst_index = _cell_shared_index(fn_xml, hidden_cell)
    if sst_index is None:
        raise ValueError(f"{footnote_sheet}!{hidden_cell} is not a shared-string cell")

    refs, _issues = _shared_string_refs(data)
    ref_count = sum(1 for ref in refs if ref["index"] == sst_index)
    if ref_count != 1:
        raise ValueError(
            f"shared-string index {sst_index} has {ref_count} worksheet references; "
            "refusing to rewrite a non-unique payload"
        )

    sst_xml = data["xl/sharedStrings.xml"].decode("utf-8")
    patched_sst = replace_shared_string(sst_xml, sst_index, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_patched_zip(
        str(source), str(output),
        {"xl/sharedStrings.xml": patched_sst.encode("utf-8")},
    )
    return {
        "source": source,
        "output": output,
        "footnote_sheet": footnote_sheet,
        "hidden_cell": hidden_cell,
        "sst_index": sst_index,
    }


def make_boundary_workbook(
    workbook_path: Union[str, Path],
    output_path: Union[str, Path],
    key: str,
    decoded_length: int,
    *,
    line_breaks: int = 0,
    acknowledged: bool = False,
) -> dict:
    """Create one disposable boundary workbook without touching the source."""
    if not acknowledged:
        raise ValueError(
            "boundary generation bypasses the production guard; pass the explicit "
            "unsafe acknowledgement and use a dummy filing only"
        )
    payload = _build_boundary_payload(decoded_length, line_breaks)
    written = _write_payload_copy(workbook_path, output_path, key, payload)
    source = written["source"]
    output = written["output"]
    inspected = inspect_workbook(output, {key})
    inspected["probe"] = {
        "source_workbook": str(source),
        "source_sha256": _sha256_bytes(source.read_bytes()),
        "key": key,
        "hidden_cell": written["hidden_cell"],
        "requested_decoded_utf16_units": decoded_length,
        "line_breaks": line_breaks,
        "production_guard_bypassed": True,
        "dummy_filing_only": True,
    }
    return inspected


def _render_pair_html(rows: int, cols: int) -> str:
    if rows < 1:
        raise ValueError("rows must be at least 1")
    if cols < 2:
        raise ValueError("cols must be at least 2")
    headers = "<th>Category</th>" + "".join(
        f"<th>Amount {index}</th>" for index in range(1, cols)
    )
    body = "".join(
        "<tr><td>Test category {row}</td>".format(row=row)
        + "".join(f"<td>{row * 1000 + col:,}</td>" for col in range(1, cols))
        + "</tr>"
        for row in range(1, rows + 1)
    )
    return (
        "<h3>COMPACT RENDER A/B — DUMMY DATA</h3>"
        "<p>Identical content; only the export decoration tier differs.</p>"
        f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"
    )


def make_render_pair(
    workbook_path: Union[str, Path],
    full_output_path: Union[str, Path],
    compact_output_path: Union[str, Path],
    key: str,
    *,
    rows: int = 25,
    cols: int = 6,
    acknowledged: bool = False,
) -> dict:
    """Create identical-content full/compact workbooks for TX27 visual A/B."""
    if not acknowledged:
        raise ValueError(
            "render-pair generation is for dummy filings only; pass the explicit "
            "unsafe acknowledgement"
        )
    from bs4 import BeautifulSoup  # repo dependency; not used by offline_fill.py
    from mtool.notes_decorate import decorate_notes_html

    raw = _render_pair_html(rows, cols)
    full_html = decorate_notes_html(raw)
    compact_html = decorate_notes_html(raw, compact=True)
    full_payload = wrap_footnote_html(full_html)
    compact_payload = wrap_footnote_html(compact_html)
    if len(full_payload) > EXCEL_CELL_CHAR_LIMIT:
        raise ValueError(
            f"full render payload is {len(full_payload)} chars; choose fewer rows"
        )
    if len(compact_payload) > EXCEL_CELL_CHAR_LIMIT:
        raise ValueError(
            f"compact render payload is {len(compact_payload)} chars; choose fewer rows"
        )
    if BeautifulSoup(full_html, "html.parser").get_text() != BeautifulSoup(
        compact_html, "html.parser"
    ).get_text():
        raise AssertionError("full and compact render-pair text diverged")

    full_written = _write_payload_copy(
        workbook_path, full_output_path, key, full_payload,
    )
    compact_written = _write_payload_copy(
        workbook_path, compact_output_path, key, compact_payload,
    )
    return {
        "schema": "mtool-render-pair/v1",
        "source_workbook": str(Path(workbook_path).resolve()),
        "key": key,
        "hidden_cell": full_written["hidden_cell"],
        "rows": rows,
        "cols": cols,
        "dummy_filing_only": True,
        "full": inspect_workbook(full_written["output"], {key}),
        "compact": inspect_workbook(compact_written["output"], {key}),
    }


def make_compact_stress_workbook(
    workbook_path: Union[str, Path],
    output_path: Union[str, Path],
    key: str,
    *,
    rows: int = 100,
    cols: int = 6,
    acknowledged: bool = False,
) -> dict:
    """Create a large compact-only workbook for the TX re-save inflation test."""
    if not acknowledged:
        raise ValueError(
            "compact-stress generation is for dummy filings only; pass the "
            "explicit unsafe acknowledgement"
        )
    from mtool.notes_decorate import decorate_notes_html

    raw = _render_pair_html(rows, cols)
    compact_html = decorate_notes_html(raw, compact=True)
    payload = wrap_footnote_html(compact_html)
    if len(payload) > EXCEL_CELL_CHAR_LIMIT:
        raise ValueError(
            f"compact stress payload is {len(payload)} chars; choose fewer rows"
        )
    written = _write_payload_copy(workbook_path, output_path, key, payload)
    report = inspect_workbook(written["output"], {key})
    report["probe"] = {
        "source_workbook": str(Path(workbook_path).resolve()),
        "key": key,
        "hidden_cell": written["hidden_cell"],
        "rows": rows,
        "cols": cols,
        "tier_forced": "compact",
        "dummy_filing_only": True,
    }
    return report


def _payload_by_key(report: dict, key: str) -> Optional[dict]:
    return next((item for item in report.get("payloads", []) if item["key"] == key), None)


def compare_workbooks(
    before: Union[str, Path], after: Union[str, Path], key: str,
) -> dict:
    """Compare one payload before and after Excel/mTool round-trip."""
    before_report = inspect_workbook(before, {key})
    after_report = inspect_workbook(after, {key})
    b = _payload_by_key(before_report, key)
    a = _payload_by_key(after_report, key)
    result = {
        "schema": "mtool-broken-file-probe-compare/v1",
        "key": key,
        "before_workbook": before_report["workbook"],
        "after_workbook": after_report["workbook"],
        "before_workbook_sha256": before_report["workbook_sha256"],
        "after_workbook_sha256": after_report["workbook_sha256"],
        "before_status": before_report["status"],
        "after_status": after_report["status"],
        "before_issues": before_report["issues"],
        "after_issues": after_report["issues"],
        "payload_present_before": b is not None and b.get("populated", False),
        "payload_present_after": a is not None and a.get("populated", False),
    }
    before_members = set(before_report.get("zip", {}).get("member_names", []))
    after_members = set(after_report.get("zip", {}).get("member_names", []))
    result["package"] = {
        "members_added": sorted(after_members - before_members),
        "members_removed": sorted(before_members - after_members),
    }
    if b and a and b.get("populated") and a.get("populated"):
        result["payload"] = {
            "stored_chars_before": b["stored_chars"],
            "stored_chars_after": a["stored_chars"],
            "stored_chars_delta": a["stored_chars"] - b["stored_chars"],
            "decoded_utf16_units_before": b["decoded_utf16_units"],
            "decoded_utf16_units_after": a["decoded_utf16_units"],
            "decoded_utf16_units_delta": (
                a["decoded_utf16_units"] - b["decoded_utf16_units"]
            ),
            "stored_payload_unchanged": b["stored_sha256"] == a["stored_sha256"],
            "decoded_payload_unchanged": b["decoded_sha256"] == a["decoded_sha256"],
            "xhtml_valid_before": b["xhtml_valid"],
            "xhtml_valid_after": a["xhtml_valid"],
        }
    return result


def _write_json(report: dict, path: Optional[str]) -> None:
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def _print_human(report: dict) -> None:
    print(f"workbook: {report['workbook']}")
    print(f"sha256:   {report.get('workbook_sha256')}")
    print(f"status:   {report['status']}")
    for issue in report.get("issues", []):
        print(f"[{issue['severity'].upper()}] {issue['code']}: {issue['detail']}")
    for payload in report.get("payloads", []):
        if not payload.get("populated"):
            continue
        print(
            f"{payload['key']} {report.get('footnote_sheet')}!{payload['hidden_cell']}: "
            f"stored={payload['stored_chars']}, "
            f"decoded_utf16={payload['decoded_utf16_units']}, "
            f"xhtml_valid={payload['xhtml_valid']}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect", help="run read-only static checks")
    inspect_p.add_argument("--workbook", required=True)
    inspect_p.add_argument("--keys", nargs="*", default=[])
    inspect_p.add_argument("--json", action="store_true", dest="as_json")
    inspect_p.add_argument("--json-out")

    boundary_p = sub.add_parser(
        "make-boundary", help="create a disposable over/at-limit test workbook"
    )
    boundary_p.add_argument("--workbook", required=True)
    boundary_p.add_argument("--output", required=True)
    boundary_p.add_argument("--key", required=True)
    boundary_p.add_argument("--decoded-length", required=True, type=int)
    boundary_p.add_argument("--line-breaks", type=int, default=0)
    boundary_p.add_argument("--unsafe-boundary-probe", action="store_true")
    boundary_p.add_argument("--json-out")

    render_p = sub.add_parser(
        "make-render-pair",
        help="create identical-content full/compact workbooks for mTool A/B",
    )
    render_p.add_argument("--workbook", required=True)
    render_p.add_argument("--full-output", required=True)
    render_p.add_argument("--compact-output", required=True)
    render_p.add_argument("--key", required=True)
    render_p.add_argument("--rows", type=int, default=25)
    render_p.add_argument("--cols", type=int, default=6)
    render_p.add_argument("--unsafe-render-probe", action="store_true")
    render_p.add_argument("--json-out")

    stress_p = sub.add_parser(
        "make-compact-stress",
        help="create a large compact-only workbook for mTool re-save testing",
    )
    stress_p.add_argument("--workbook", required=True)
    stress_p.add_argument("--output", required=True)
    stress_p.add_argument("--key", required=True)
    stress_p.add_argument("--rows", type=int, default=100)
    stress_p.add_argument("--cols", type=int, default=6)
    stress_p.add_argument("--unsafe-render-probe", action="store_true")
    stress_p.add_argument("--json-out")

    compare_p = sub.add_parser(
        "compare", help="compare a payload before/after Excel or mTool save"
    )
    compare_p.add_argument("--before", required=True)
    compare_p.add_argument("--after", required=True)
    compare_p.add_argument("--key", required=True)
    compare_p.add_argument("--json-out")

    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            report = inspect_workbook(args.workbook, set(args.keys))
            if args.json_out:
                _write_json(report, args.json_out)
            if args.as_json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            elif not args.json_out:
                _print_human(report)
            return 1 if any(i["severity"] == "error" for i in report["issues"]) else 0
        if args.command == "make-boundary":
            report = make_boundary_workbook(
                args.workbook, args.output, args.key, args.decoded_length,
                line_breaks=args.line_breaks,
                acknowledged=args.unsafe_boundary_probe,
            )
            _write_json(report, args.json_out)
            return 0
        if args.command == "make-render-pair":
            report = make_render_pair(
                args.workbook, args.full_output, args.compact_output, args.key,
                rows=args.rows, cols=args.cols,
                acknowledged=args.unsafe_render_probe,
            )
            _write_json(report, args.json_out)
            return 0
        if args.command == "make-compact-stress":
            report = make_compact_stress_workbook(
                args.workbook, args.output, args.key,
                rows=args.rows, cols=args.cols,
                acknowledged=args.unsafe_render_probe,
            )
            _write_json(report, args.json_out)
            return 0
        report = compare_workbooks(args.before, args.after, args.key)
        _write_json(report, args.json_out)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
