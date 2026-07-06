"""Dump a NATIVE mTool note text-block payload verbatim — a FALLBACK diagnostic
(docs/MTOOL-NOTES-FORMAT-RECON.md).

Formatting fidelity is already handled: the notes fill path DECORATES the
style-free DB HTML with inline styles before writing it (mtool/notes_decorate.py,
the backend port of the render-proven clipboard decorator), so mTool's TX27
editor renders borders/fills/font/alignment. This tool is only needed if some
SPECIFIC decorated construct still renders wrong in mTool — then dump how mTool
itself stores that same construct natively and compare, to see which exact
inline-style form TX Text Control (``TX27_HTM``) wants. It reads a genuine mTool
workbook and prints each populated ``+FootnoteTexts`` payload exactly as stored
(one level of XML-unescaping, the way mTool reads it back).

    # dump every populated native payload:
    python mtool/examples/dump_fn_payload.py --workbook original.xlsx

    # just the ones you formatted (find the fn_* via the notes-preview / inspect):
    python mtool/examples/dump_fn_payload.py --workbook original.xlsx --keys fn_14 fn_22

    # --repr shows exact whitespace / _x000D_ tokens (survives email/paste):
    python mtool/examples/dump_fn_payload.py --workbook original.xlsx --keys fn_14 --repr

Read-only. Reuses the proven offline_fill readers (stdlib only).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python mtool/examples/dump_fn_payload.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mtool.offline_fill import (  # noqa: E402
    find_footnote_sheet, get_sheet_paths, get_shared_strings,
    load_workbook_entries, read_footnote_rows)

_RULE = "=" * 72


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", required=True,
                    help="a genuine mTool .xlsx with formatted note(s)")
    ap.add_argument("--keys", nargs="*", default=[],
                    help="restrict to these fn_* keys (default: all populated)")
    ap.add_argument("--repr", action="store_true", dest="as_repr",
                    help="print repr() so whitespace / _x000D_ tokens are exact")
    args = ap.parse_args(argv)

    _, data, _ = load_workbook_entries(args.workbook)
    sheet_paths = get_sheet_paths(data)
    sst = get_shared_strings(data)
    fn_sheet = find_footnote_sheet(sheet_paths)
    rows = read_footnote_rows(data, sheet_paths, sst, fn_sheet)

    wanted = set(args.keys)
    populated = {k: v for k, v in rows.items()
                 if v["payload_populated"] and (not wanted or k in wanted)}

    print(f"workbook: {args.workbook}")
    print(f"footnote sheet: {fn_sheet}")
    print(f"populated payloads: {len(populated)} "
          f"(of {len(rows)} fn_* rows)")
    if wanted:
        missing = sorted(wanted - set(rows))
        empty = sorted(k for k in wanted
                       if k in rows and not rows[k]["payload_populated"])
        if missing:
            print(f"  requested but absent: {missing}")
        if empty:
            print(f"  requested but empty:  {empty}")

    for key in sorted(populated, key=lambda k: populated[k]["row"]):
        fn = populated[key]
        text = fn["payload_text"]
        print(f"\n{_RULE}\n{key}  (row {fn['row']}, col {fn['payload_col']}, "
              f"{len(text)} chars)\n{_RULE}")
        print(repr(text) if args.as_repr else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
