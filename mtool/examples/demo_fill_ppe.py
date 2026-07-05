"""End-to-end demo: automatically fill a PPE note into an mTool template.

Shows the full prose-note chain with NO hand-picked cell/key:

    sample PPE HTML  ->  find the field on the notes sheet by LABEL
                     ->  resolve its fn_* defined name
                     ->  replace the hidden +FootnoteTexts payload
                     ->  read the payload back and prove it landed

Run against a real mTool template (the interesting case)::

    python mtool/examples/demo_fill_ppe.py --workbook resmed.xlsx

Or with no args, it builds a small synthetic mTool-shaped workbook (a fresh
PPE text-block with an EMPTY payload) so you can watch the mechanism work
without the real file::

    python mtool/examples/demo_fill_ppe.py

Only the standard library + mtool.offline_fill are used.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mtool.offline_fill import (  # noqa: E402
    fill_footnotes,
    get_sheet_paths,
    get_shared_strings,
    inspect_footnotes,
    load_workbook_entries,
    read_footnote_rows,
    resolve_footnote_by_label,
)

# A rich PPE note in exactly the shape our extraction pipeline emits: heading,
# justified policy prose, an indented paragraph, and a bordered totals table.
SAMPLE_PPE_HTML = (
    '<h3>Property, plant and equipment</h3>'
    '<p style="text-align:justify;">The Group\'s property, plant and equipment '
    'comprise leasehold buildings, plant and machinery, office equipment and '
    'renovation assets used in its operations.</p>'
    '<p style="text-align:justify;margin-left:18pt;">Depreciation is recognised '
    'on a straight-line basis over the estimated useful lives of the assets.</p>'
    '<table style="border-collapse:collapse;width:100%;">'
    '<tr><th style="border-bottom:1px solid #1F3864;">Class of assets</th>'
    '<th style="border-bottom:1px solid #1F3864;text-align:right;">'
    "Carrying amount RM'000</th></tr>"
    '<tr><td>Plant and machinery</td>'
    '<td style="text-align:right;">8,265</td></tr>'
    '<tr><td>Office equipment &amp; renovation</td>'
    '<td style="text-align:right;">1,075</td></tr>'
    '<tr><td style="border-top:3px double #1F3864;"><strong>Total</strong></td>'
    '<td style="border-top:3px double #1F3864;text-align:right;">'
    '<strong>9,340</strong></td></tr>'
    '</table>'
)

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ws(rows: dict) -> str:
    body = []
    for r in sorted(rows):
        cells = "".join(
            f'<c r="{col}{r}"/>' if idx is None
            else f'<c r="{col}{r}" t="s"><v>{idx}</v></c>'
            for col, idx in rows[r])
        body.append(f'<row r="{r}">{cells}</row>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_NS_MAIN}"><sheetData>'
            + "".join(body) + "</sheetData></worksheet>")


def build_demo_template(path: str) -> str:
    """A minimal but faithful mTool-shaped workbook: a 'Notes-Listofnotes'
    sheet with three disclosure text-blocks (corporate info, PPE, accounting
    policies), the PPE one with a FRESH (empty) +FootnoteTexts payload."""
    strings = [
        "*Disclosure of corporate information [text block]",             # 0
        "[Text block added]",                                            # 1
        "*Disclosure of property, plant and equipment [text block]",     # 2
        "*Disclosure of material accounting policies [text block]",      # 3
        "fn_11", "fn_25", "fn_13", "Notes-Listofnotes",                  # 4-7
        "<html><body><p>Existing corporate-info note.</p></body></html>",  # 8
        "<html><body><p>Existing policies note.</p></body></html>",       # 9
    ]
    sst = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<sst xmlns="{_NS_MAIN}" count="{len(strings)}" '
           f'uniqueCount="{len(strings)}">'
           + "".join(f"<si><t>{_esc(s)}</t></si>" for s in strings) + "</sst>")
    # Visible notes sheet: label in D, trigger in E.
    sheet1 = _ws({12: [("D", 0), ("E", 1)],
                  17: [("D", 2), ("E", 1)],
                  20: [("D", 3), ("E", 1)]})
    # Hidden payload sheet: fn_25 (PPE) has an EMPTY payload cell.
    sheet2 = _ws({11: [("A", 4), ("B", 7), ("C", 8)],
                  25: [("A", 5), ("B", 7), ("C", None)],
                  13: [("A", 6), ("B", 7), ("C", 9)]})
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}"><sheets>'
        '<sheet name="Notes-Listofnotes" sheetId="1" r:id="rId1"/>'
        '<sheet name="+FootnoteTexts" sheetId="2" r:id="rId2"/></sheets>'
        '<definedNames>'
        "<definedName name=\"fn_11\">'Notes-Listofnotes'!$E$12</definedName>"
        "<definedName name=\"fn_25\">'Notes-Listofnotes'!$E$17</definedName>"
        "<definedName name=\"fn_13\">'Notes-Listofnotes'!$E$20</definedName>"
        '</definedNames></workbook>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    wb_rels = (
        f'<Relationships xmlns="{pkg}">'
        f'<Relationship Id="rId1" Type="{rel}/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{rel}/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        f'<Relationship Id="rId3" Type="{rel}/sharedStrings" '
        'Target="sharedStrings.xml"/></Relationships>')
    ct = ("http://schemas.openxmlformats.org/officeDocument/2006/"
          "spreadsheetml")
    content_types = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'content-types"><Default Extension="rels" ContentType="application/'
        f'vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        f'{ct}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" '
        f'ContentType="application/vnd.{ct}.worksheet+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet2.xml" '
        f'ContentType="application/vnd.{ct}.worksheet+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" '
        f'ContentType="application/vnd.{ct}.sharedStrings+xml"/></Types>')
    root_rels = (
        f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" '
        f'Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet1)
        z.writestr("xl/worksheets/sheet2.xml", sheet2)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workbook", help="mTool template (defaults to a built-in "
                    "synthetic demo workbook)")
    ap.add_argument("--label", default="Property, plant and equipment",
                    help="the note label to find and fill")
    ap.add_argument("--output", help="filled workbook path")
    args = ap.parse_args(argv)

    if args.workbook:
        workbook = args.workbook
        synthetic = False
    else:
        workbook = str(Path(__file__).with_name("_demo_mtool_template.xlsx"))
        build_demo_template(workbook)
        synthetic = True
    output = args.output or workbook.replace(".xlsx", "_filled.xlsx")

    print(f"workbook: {workbook}" + ("  (synthetic demo)" if synthetic else ""))
    print("-" * 70)

    # 1. Discover the template's prose-note targets from the file itself.
    _, data, _ = load_workbook_entries(workbook)
    info = inspect_footnotes(data)
    targets = info["targets"]
    print(f"[1] discovered {len(targets)} fn_* note target(s) on "
          f"{info['footnote_sheet']}")
    for t in targets[:6]:
        lbl = " | ".join(t["row_text"].values()) or "(no visible label)"
        print(f"      {t['key']:<6} {t['sheet']}!{t['cell']:<6} {lbl[:60]}")

    # 2. The payload we want to file.
    print(f"\n[2] sample PPE payload ({len(SAMPLE_PPE_HTML)} chars): "
          f"{SAMPLE_PPE_HTML[:56]}…")

    # 3. Resolve the label -> fn_* automatically (no hand-picked cell/key).
    res = resolve_footnote_by_label(args.label, targets)
    print(f"\n[3] resolve label {args.label!r}: {res['status']}")
    if res["status"] != "resolved":
        print(f"      {res.get('detail')}")
        print("      (is this note a prose text-block on this template?)")
        return 1
    print(f"      -> {res['key']}  (matched {res['matched_label']!r}, "
          f"similarity {res['ratio']})")

    # 4. Fill it — label targeting, so fill_footnotes does the same resolution.
    report = fill_footnotes(
        workbook,
        {"footnotes": [{"label": args.label, "html": SAMPLE_PPE_HTML}]},
        output_path=output)
    print(f"\n[4] fill -> status={report['status']}")
    for w in report["footnotes_written"]:
        print(f"      wrote {w['key']} payload {w['hidden_sheet']}!"
              f"{w['hidden_cell']} ({w['action']})")
    for bucket in ("unresolved", "footnote_mismatches", "errors"):
        for e in report[bucket]:
            print(f"      {bucket}: {e}")

    # 5. Prove it by reading the payload straight back out of the new file.
    _, out_data, _ = load_workbook_entries(output)
    out_rows = read_footnote_rows(out_data, get_sheet_paths(out_data),
                                  get_shared_strings(out_data))
    payload = out_rows.get(res["key"], {}).get("payload_text") or ""
    print(f"\n[5] read-back {res['key']} payload ({len(payload)} chars):")
    for marker in ("Property, plant and equipment", "<table", "9,340",
                   "3px double #1F3864"):
        print(f"      {'✓' if marker in payload else '✗'} contains "
              f"{marker!r}")
    print(f"\nfilled workbook: {output}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
