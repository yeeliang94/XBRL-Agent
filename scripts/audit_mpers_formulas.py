#!/usr/bin/env python3
"""Complete MPERS formula audit report.

Compares every SUM formula emitted by `scripts/generate_mpers_templates.py`
against the SSM MPERS calculation linkbase. Run when a new SSM taxonomy
version drops to catch any parent/child drift in the generated bundle.

Run: python3 scripts/audit_mpers_formulas.py > audit_report.txt
"""
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
import openpyxl
import re

REPO_ROOT = Path(__file__).resolve().parent
MPERS_TAXONOMY_DIR = REPO_ROOT / "SSMxT_2022v1.0/rep/ssm/ca-2016/fs/mpers"
TEMPLATES_COMPANY = REPO_ROOT / "XBRL-template-MPERS/Company"
TEMPLATES_GROUP = REPO_ROOT / "XBRL-template-MPERS/Group"

_PRE_TO_CALC_ROLE = {
    "210000": "200100", "210100": "200200",
    "220000": "200100", "220100": "200200",
    "310000": "300100", "310100": "300200",
    "320000": "300100", "320100": "300200",
    "410000": "400100", "420000": "400100",
    "510000": "500100", "520000": "500100",
    "610000": "610000", "620000": "620000",
}

_TEMPLATE_MAPPING = [
    ("01-SOFP-CuNonCu.xlsx", ["210000", "210100"]),
    ("02-SOFP-OrderOfLiquidity.xlsx", ["220000", "220100"]),
    ("03-SOPL-Function.xlsx", ["310000", "310100"]),
    ("04-SOPL-Nature.xlsx", ["320000", "320100"]),
    ("05-SOCI-BeforeTax.xlsx", ["420000"]),
    ("06-SOCI-NetOfTax.xlsx", ["410000"]),
    ("07-SOCF-Indirect.xlsx", ["520000"]),
    ("08-SOCF-Direct.xlsx", ["510000"]),
    ("09-SOCIE.xlsx", ["610000"]),
    ("10-SoRE.xlsx", ["620000"]),
]

_SHEET_NAMES = {
    "01-SOFP-CuNonCu.xlsx": ["SOFP-CuNonCu", "SOFP-Sub-CuNonCu"],
    "02-SOFP-OrderOfLiquidity.xlsx": ["SOFP-OrdOfLiq", "SOFP-Sub-OrdOfLiq"],
    "03-SOPL-Function.xlsx": ["SOPL-Function", "SOPL-Analysis-Function"],
    "04-SOPL-Nature.xlsx": ["SOPL-Nature", "SOPL-Analysis-Nature"],
    "05-SOCI-BeforeTax.xlsx": ["SOCI-BeforeOfTax"],
    "06-SOCI-NetOfTax.xlsx": ["SOCI-NetOfTax"],
    "07-SOCF-Indirect.xlsx": ["SOCF-Indirect"],
    "08-SOCF-Direct.xlsx": ["SOCF-Direct"],
    "09-SOCIE.xlsx": ["SOCIE"],
    "10-SoRE.xlsx": ["SoRE"],
}

NS = {
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
}

def concept_id_from_href(href: str) -> str:
    return href.split("#", 1)[-1]

def parse_calc_linkbase(calc_file_path: Path) -> dict[str, list[tuple[str, int]]]:
    tree = ET.parse(calc_file_path)
    root = tree.getroot()
    loc_map = {}
    pending = defaultdict(list)

    for calc_link in root.iter(f"{{{NS['link']}}}calculationLink"):
        for elem in calc_link:
            tag = elem.tag.split("}", 1)[-1]
            if tag == "loc":
                key = elem.get(f"{{{NS['xlink']}}}label")
                href = elem.get(f"{{{NS['xlink']}}}href")
                if key and href:
                    loc_map[key] = concept_id_from_href(href)
            elif tag == "calculationArc":
                frm = elem.get(f"{{{NS['xlink']}}}from")
                to = elem.get(f"{{{NS['xlink']}}}to")
                if not (frm and to):
                    continue
                parent_concept = loc_map.get(frm)
                child_concept = loc_map.get(to)
                if not (parent_concept and child_concept):
                    continue
                try:
                    weight = int(float(elem.get("weight", "1")))
                except ValueError:
                    weight = 1
                try:
                    order = float(elem.get("order", "0"))
                except ValueError:
                    order = 0.0
                pending[parent_concept].append((order, child_concept, weight))

    result = {}
    for parent, entries in pending.items():
        entries.sort(key=lambda x: x[0])
        result[parent] = [(child, weight) for _order, child, weight in entries]
    return result

def walk_pre_linkbase(pre_file_path: Path) -> list[str]:
    tree = ET.parse(pre_file_path)
    root = tree.getroot()
    rows = []
    for pres_link in root.iter(f"{{{NS['link']}}}presentationLink"):
        loc_map = {}
        children = defaultdict(list)
        all_froms = set()
        all_tos = set()

        for elem in pres_link:
            tag = elem.tag.split("}", 1)[-1]
            if tag == "loc":
                key = elem.get(f"{{{NS['xlink']}}}label")
                href = elem.get(f"{{{NS['xlink']}}}href")
                if key and href:
                    loc_map[key] = concept_id_from_href(href)
            elif tag == "presentationArc":
                frm = elem.get(f"{{{NS['xlink']}}}from")
                to = elem.get(f"{{{NS['xlink']}}}to")
                if not (frm and to):
                    continue
                order_raw = elem.get("order", "0")
                try:
                    order = float(order_raw)
                except ValueError:
                    order = 0.0
                children[frm].append((order, to))
                all_froms.add(frm)
                all_tos.add(to)

        roots = [lbl for lbl in loc_map if lbl in all_froms and lbl not in all_tos]
        if not roots:
            roots = [lbl for lbl in loc_map if lbl not in all_tos]

        def dfs(label: str):
            concept_id = loc_map.get(label)
            if not concept_id:
                return
            rows.append(concept_id)
            for _order, child_label in sorted(children[label], key=lambda x: x[0]):
                dfs(child_label)

        for root_label in roots:
            dfs(root_label)

    return rows

def parse_formula(formula_str: str) -> list[tuple[str, int]]:
    if not formula_str or not formula_str.startswith("="):
        return []
    expr = formula_str[1:]
    expr = expr.replace("-", "+-")
    terms = [t for t in expr.split("+") if t]
    result = []
    for term in terms:
        if "*" in term:
            weight_s, ref = term.split("*", 1)
            try:
                weight = int(float(weight_s))
            except ValueError:
                weight = 1
        else:
            ref, weight = term, 1
        result.append((ref, weight))
    return result

def extract_col_row(cell_ref: str) -> tuple[str, int] | None:
    match = re.match(r"^([A-Z]+)(\d+)$", cell_ref)
    if match:
        return (match.group(1), int(match.group(2)))
    return None

def audit_template(template_path: Path, level: str) -> dict:
    filename = template_path.name
    role_numbers = None
    for fname, rns in _TEMPLATE_MAPPING:
        if fname == filename:
            role_numbers = rns
            break

    if not role_numbers:
        return {"error": f"Unknown template {filename}"}

    sheet_names = _SHEET_NAMES.get(filename, [])
    wb = openpyxl.load_workbook(template_path)
    result = {"filename": filename, "level": level, "sheets": {}}

    for sheet_name, role_number in zip(sheet_names, role_numbers):
        if sheet_name not in wb.sheetnames:
            result["sheets"][sheet_name] = {"error": f"Sheet not found"}
            continue

        if filename == "09-SOCIE.xlsx" and level == "group":
            result["sheets"][sheet_name] = {"status": "socie_group_4block"}
            continue

        ws = wb[sheet_name]
        calc_role = _PRE_TO_CALC_ROLE.get(role_number)
        calc_map = {}
        if calc_role:
            calc_file = MPERS_TAXONOMY_DIR / f"cal_ssmt-fs-mpers_2022-12-31_role-{calc_role}.xml"
            if calc_file.exists():
                calc_map = parse_calc_linkbase(calc_file)

        pre_file = MPERS_TAXONOMY_DIR / f"pre_ssmt-fs-mpers_2022-12-31_role-{role_number}.xml"
        pre_rows = walk_pre_linkbase(pre_file)
        concept_to_row = {cid: 3 + idx for idx, cid in enumerate(pre_rows)}

        sheet_result = {
            "calc_parents": len(calc_map),
            "formulas_correct": 0,
            "formulas_wrong": 0,
            "formulas_missing": 0,
            "dropped_children_count": 0,
            "issues": []
        }

        if level == "company":
            value_cols = ["B", "C"]
        elif filename == "09-SOCIE.xlsx":
            sheet_result["note"] = "SOCIE Group 4-block layout"
            result["sheets"][sheet_name] = sheet_result
            continue
        else:
            value_cols = ["B", "C", "D", "E"]

        for parent_concept, children in calc_map.items():
            parent_row = concept_to_row.get(parent_concept)
            if parent_row is None:
                continue

            expected_parts = []
            dropped_count = 0
            for child_concept, weight in children:
                child_row = concept_to_row.get(child_concept)
                if child_row is None:
                    dropped_count += 1
                else:
                    expected_parts.append((child_row, weight))

            if dropped_count > 0:
                sheet_result["dropped_children_count"] += 1

            if not expected_parts:
                continue

            for col_letter in value_cols:
                col_idx = ord(col_letter) - ord("A") + 1
                formula_cell = ws.cell(row=parent_row, column=col_idx)
                formula_str = formula_cell.value

                if not formula_str or not isinstance(formula_str, str) or not formula_str.startswith("="):
                    sheet_result["formulas_missing"] += 1
                    sheet_result["issues"].append({
                        "type": "missing",
                        "parent": parent_concept,
                        "cell": f"{col_letter}{parent_row}",
                    })
                    continue

                parsed = parse_formula(formula_str)
                parsed_pairs = []
                for cell_ref, weight in parsed:
                    cr = extract_col_row(cell_ref)
                    if cr:
                        _, row = cr
                        parsed_pairs.append((row, weight))

                expected_set = set(expected_parts)
                parsed_set = set(parsed_pairs)

                if expected_set == parsed_set:
                    sheet_result["formulas_correct"] += 1
                else:
                    sheet_result["formulas_wrong"] += 1
                    sheet_result["issues"].append({
                        "type": "mismatch",
                        "parent": parent_concept,
                        "cell": f"{col_letter}{parent_row}",
                        "formula": formula_str[:80],
                    })

        result["sheets"][sheet_name] = sheet_result

    return result

def main():
    print("=" * 100)
    print("MPERS FORMULA AUDIT — DETAILED REPORT")
    print("=" * 100)
    print()

    company_totals = {
        "templates": 0,
        "sheets": 0,
        "calc_parents": 0,
        "formulas_correct": 0,
        "formulas_wrong": 0,
        "formulas_missing": 0,
        "parents_with_dropped_children": 0,
    }

    group_totals = {
        "templates": 0,
        "sheets": 0,
        "calc_parents": 0,
        "formulas_correct": 0,
        "formulas_wrong": 0,
        "formulas_missing": 0,
        "parents_with_dropped_children": 0,
    }

    print("COMPANY LEVEL AUDIT")
    print("-" * 100)
    for filename, _ in _TEMPLATE_MAPPING[:10]:
        template_path = TEMPLATES_COMPANY / filename
        if not template_path.exists():
            print(f"{filename}: TEMPLATE NOT FOUND")
            continue

        result = audit_template(template_path, level="company")
        company_totals["templates"] += 1

        for sheet_name, sheet_result in result.get("sheets", {}).items():
            company_totals["sheets"] += 1
            company_totals["calc_parents"] += sheet_result.get("calc_parents", 0)
            correct = sheet_result.get("formulas_correct", 0)
            wrong = sheet_result.get("formulas_wrong", 0)
            missing = sheet_result.get("formulas_missing", 0)
            dropped = sheet_result.get("dropped_children_count", 0)

            company_totals["formulas_correct"] += correct
            company_totals["formulas_wrong"] += wrong
            company_totals["formulas_missing"] += missing
            company_totals["parents_with_dropped_children"] += dropped

            status = "OK" if (wrong == 0 and missing == 0) else "ISSUES"
            print(f"{filename} [{sheet_name}]: {status}")
            print(f"  Calc parents: {sheet_result.get('calc_parents', 0)}, "
                  f"Correct: {correct}, Wrong: {wrong}, Missing: {missing}, "
                  f"Dropped: {dropped}")
            if sheet_result.get("issues"):
                for issue in sheet_result["issues"][:3]:
                    print(f"    - {issue['type'].upper()}: {issue['cell']}")

    print()
    print("GROUP LEVEL AUDIT")
    print("-" * 100)
    for filename, _ in _TEMPLATE_MAPPING[:10]:
        template_path = TEMPLATES_GROUP / filename
        if not template_path.exists():
            print(f"{filename}: TEMPLATE NOT FOUND")
            continue

        result = audit_template(template_path, level="group")
        group_totals["templates"] += 1

        for sheet_name, sheet_result in result.get("sheets", {}).items():
            if "socie_group_4block" in sheet_result.get("status", ""):
                print(f"{filename} [{sheet_name}]: SOCIE GROUP 4-BLOCK LAYOUT (no formulas)")
                continue

            group_totals["sheets"] += 1
            group_totals["calc_parents"] += sheet_result.get("calc_parents", 0)
            correct = sheet_result.get("formulas_correct", 0)
            wrong = sheet_result.get("formulas_wrong", 0)
            missing = sheet_result.get("formulas_missing", 0)
            dropped = sheet_result.get("dropped_children_count", 0)

            group_totals["formulas_correct"] += correct
            group_totals["formulas_wrong"] += wrong
            group_totals["formulas_missing"] += missing
            group_totals["parents_with_dropped_children"] += dropped

            status = "OK" if (wrong == 0 and missing == 0) else "ISSUES"
            print(f"{filename} [{sheet_name}]: {status}")
            print(f"  Calc parents: {sheet_result.get('calc_parents', 0)}, "
                  f"Correct: {correct}, Wrong: {wrong}, Missing: {missing}, "
                  f"Dropped: {dropped}")

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print()
    print(f"COMPANY LEVEL (value columns B, C):")
    print(f"  Templates audited: {company_totals['templates']}")
    print(f"  Sheets audited: {company_totals['sheets']}")
    print(f"  Calc parents in linkbase: {company_totals['calc_parents']}")
    print(f"  Formulas correct: {company_totals['formulas_correct']}")
    print(f"  Formulas with mismatches: {company_totals['formulas_wrong']}")
    print(f"  Formulas missing: {company_totals['formulas_missing']}")
    print(f"  Parents with dropped children (cross-sheet): {company_totals['parents_with_dropped_children']}")
    print()
    print(f"GROUP LEVEL (value columns B, C, D, E; SOCIE excepted):")
    print(f"  Templates audited: {group_totals['templates']}")
    print(f"  Sheets audited: {group_totals['sheets']}")
    print(f"  Calc parents in linkbase: {group_totals['calc_parents']}")
    print(f"  Formulas correct: {group_totals['formulas_correct']}")
    print(f"  Formulas with mismatches: {group_totals['formulas_wrong']}")
    print(f"  Formulas missing: {group_totals['formulas_missing']}")
    print(f"  Parents with dropped children (cross-sheet): {group_totals['parents_with_dropped_children']}")
    print()

    if (company_totals["formulas_wrong"] == 0 and company_totals["formulas_missing"] == 0 and
        group_totals["formulas_wrong"] == 0 and group_totals["formulas_missing"] == 0):
        print("RESULT: ALL FORMULAS CORRECT")
    else:
        print("RESULT: ISSUES FOUND")

if __name__ == "__main__":
    main()
