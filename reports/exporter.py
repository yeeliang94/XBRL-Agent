"""Resolve report scalars only when taxonomy identity and input encoding are explicit."""
from __future__ import annotations

from datetime import date
import re
import xml.etree.ElementTree as ET

from mtool.offline_fill import _parse_defined_ref, get_sheet_paths, protected_input_cells, is_locked_input
from mtool.template_map import index_workbook

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _contains(sqref: str, addr: str) -> bool:
    from mtool.offline_fill import split_ref, col_to_idx
    col, row = split_ref(addr)
    for region in sqref.split():
        ends = region.replace('$', '').split(':')
        c1, r1 = split_ref(ends[0]); c2, r2 = split_ref(ends[-1])
        if r1 <= row <= r2 and col_to_idx(c1) <= col_to_idx(col) <= col_to_idx(c2):
            return True
    return False


_TABLE_END = {"#LAYOUTSCER#", "#LAYOUTECER#"}


def _column_inputs(cells: dict, protection: dict, sheet: str, row: int, col: str) -> list[tuple[str, str]]:
    """Unlocked inputs below a column-header marker, within the same mTool table.

    Table roles such as auditor details tag each column once in a header row
    and take values in a typing row further down. Only a header row carrying
    several taxonomy markers qualifies, and the search stops at the table end.
    """
    header = cells[sheet].get(row, {})
    if sum("#ssmt_" in str(value) for _, value in header.values()) < 2:
        return []
    found = []
    for below in sorted(r for r in cells[sheet] if row < r <= row + 20):
        values = cells[sheet][below]
        if any(str(value) in _TABLE_END for _, value in values.values()):
            break
        kind = values.get(col, (None, None))[0]
        if kind in {"E", "N", "S"} and not is_locked_input(protection, sheet, f"{col}{below}"):
            found.append((sheet, f"{col}{below}"))
    return found


def resolve_reports(data: dict, report_snapshot: dict, selected_sheets=None, occupied=()) -> dict:
    occurrences, cells = index_workbook(data)
    paths = get_sheet_paths(data)
    protection = protected_input_cells(data, paths)
    writes, unresolved = [], []
    used = set(occupied)
    workbook = ET.fromstring(data["xl/workbook.xml"])
    names = {}
    for name in workbook.findall("m:definedNames/m:definedName", NS):
        bound = _parse_defined_ref(name.text or "")
        if bound:
            names.setdefault(name.get("name"), set()).add(bound)
    props = workbook.find("m:workbookPr", NS)
    epoch = date(1904, 1, 1) if props is not None and props.get("date1904") in {"1", "true"} else date(1899, 12, 30)
    for report in report_snapshot["reports"]:
        if selected_sheets is not None and report["sheet"] not in selected_sheets:
            continue
        # Shared concepts such as AddressOne also occur in filing information.
        # A report name or its unique narrative marker establishes role scope.
        report_sheets = {report["sheet"]} & paths.keys()
        for narrative in report["fields"]:
            if narrative["kind"] == "html":
                report_sheets.update(sheet for sheet, _, _ in occurrences.get(narrative["concept"], []))
        for field in report["fields"]:
            if field["kind"] == "html":
                if not field["value"] and field["value_status"] != "cleared":
                    unresolved.append({"sheet": report["sheet"], "label": field["label"], "reason": "report_narrative_missing"})
                continue
            base = {"source_sheet": report["sheet"], "label": field["label"], "concept": field["concept"], "value": field["value"], "kind": field["kind"]}
            if field["value_status"] == "unresolved" or (field["value"] is None and field["value_status"] != "cleared"):
                unresolved.append({**base, "reason": "not_disclosed" if field["value_status"] == "not_disclosed" else "assessment_incomplete"})
                continue
            candidates = set()
            # A defined name equal to the taxonomy ID supplies an exact binding.
            candidates.update(names.get(field["concept"], ()))
            # Native marker rows may expose exactly one unlocked scalar input.
            # Multiple candidates remain unresolved; never guess adjacent columns.
            for sheet, row, marker_col in occurrences.get(field["concept"], []):
                if sheet not in report_sheets or sheet not in protection:
                    continue
                same_row = {(sheet, f"{col}{row}") for col, (kind, _) in cells[sheet].get(row, {}).items()
                            if col != marker_col and kind in {"E", "N", "S"}
                            and not is_locked_input(protection, sheet, f"{col}{row}")}
                # A column-header marker takes its value from the table's typing
                # row. Several typing rows (for example two auditors) stay ambiguous.
                candidates.update(same_row or _column_inputs(cells, protection, sheet, row, marker_col))
            candidates = {(sheet, addr) for sheet, addr in candidates if sheet in report_sheets}
            if len(candidates) != 1:
                unresolved.append({**base, "reason": "missing_or_ambiguous_report_destination"})
                continue
            sheet, addr = candidates.pop()
            if sheet not in paths:
                unresolved.append({**base, "reason": "report_sheet_missing"})
                continue
            if (sheet, addr) in used:
                raise ValueError("Report destination overlaps another write")
            validations = ET.fromstring(data[paths[sheet]]).findall("m:dataValidations/m:dataValidation", NS)
            validations = [v for v in validations if _contains(v.get("sqref", ""), addr)]
            if field["value_status"] == "cleared":
                base["value"], base["kind"] = "", "text"
            elif field["concept"] == "ssmt_LicenseNumberOfAuditor":
                # The taxonomy restricts this field to four digits. A longer
                # source licence cannot be shortened without choosing which
                # digits identify the auditor; retain it for human review.
                if not re.fullmatch(r"[0-9]{4}", str(field["value"])):
                    unresolved.append({**base, "reason": "native_identifier_encoding_unverified",
                        "detail": "mTool requires a four-digit auditor licence. Confirm the licence in Reports before preparing this field."})
                    continue
            elif field["concept"] == "ssmt_RegistrationNumberOfAuditFirm":
                # Native mTool asks for AF plus four digits, even when the
                # source also discloses the firm's separate LLP registration.
                numbers = set(re.findall(r"(?<![A-Za-z0-9])AF\s*([0-9]+)(?![A-Za-z0-9])",
                                         str(field["value"]), flags=re.IGNORECASE))
                if len(numbers) != 1 or not re.fullmatch(r"[0-9]{4}", next(iter(numbers))):
                    unresolved.append({**base, "reason": "native_identifier_encoding_unverified",
                        "detail": "mTool requires one audit firm number in AF0000 format. Confirm the number in Reports before preparing this field."})
                    continue
                base["value"] = "AF" + numbers.pop()
                if base["value"] != field["value"]:
                    base["source_value"] = field["value"]
            elif field["kind"] == "choice":
                literal = next((v.findtext("m:formula1", "", NS) for v in validations if v.get("type") == "list"), "")
                choices = literal[1:-1].split(',') if literal.startswith('"') and literal.endswith('"') else []
                if field["value"] not in choices:
                    unresolved.append({**base, "reason": "native_selection_encoding_unverified"})
                    continue
            elif field["kind"] == "date":
                if not any(v.get("type") == "date" for v in validations):
                    unresolved.append({**base, "reason": "native_date_encoding_unverified"})
                    continue
                base["value"] = (date.fromisoformat(field["value"]) - epoch).days
                base["kind"] = "date_serial"
            used.add((sheet, addr))
            writes.append({**base, "sheet": sheet, "cell": addr})
    return {"writes": writes, "unresolved": unresolved, "snapshot": {"digest": report_snapshot["digest"]}}
