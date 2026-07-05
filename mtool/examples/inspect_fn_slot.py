"""Forensic diff: find the artifact a NATIVE mTool note slot has that a
hand-created (synthetic) one lacks.

mTool is a TX Text Control macro add-in, so a "real" popup-backed note is
likely registered in a workbook part beyond the visible trigger cell, the
fn_* defined name, the +FootnoteTexts row, and the shared-string payload.
This tool lists every zip part and shows, per fn_* key (and any extra search
term such as a cell ref), which parts reference it — so a part that mentions a
NATIVE key but not the synthetic key is the smoking gun.

    # one file that contains BOTH a native slot and the created one:
    python mtool/examples/inspect_fn_slot.py --workbook synthetic.xlsx \
        --keys fn_14 fn_37 --terms E132 E25

    # or run once per file and diff the "appears in parts" lists:
    python mtool/examples/inspect_fn_slot.py --workbook original.xlsx --keys fn_14
    python mtool/examples/inspect_fn_slot.py --workbook synthetic.xlsx --keys fn_37

Read-only, standard library only.
"""
from __future__ import annotations

import argparse
import re
import zipfile

_STANDARD_PREFIXES = ("[Content_Types]", "_rels/", "docProps/", "xl/")
_REGISTRY_HINTS = ("custom", "vba", "ctrlprop", "activex", "drawing",
                   "docprops", "metadata", "ctrlProp", "customData")


def _search(part_bytes: bytes, needle: str):
    """Return context snippets (or a binary-hit marker) for a needle."""
    try:
        text = part_bytes.decode("utf-8", "replace")
    except Exception:
        return ["<binary match>"] if needle.encode() in part_bytes else []
    out = []
    for m in re.finditer(re.escape(needle), text):
        s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
        out.append(re.sub(r"\s+", " ", text[s:e]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", required=True)
    ap.add_argument("--keys", nargs="+", required=True,
                    help="fn_* keys to trace (e.g. fn_14 fn_37)")
    ap.add_argument("--terms", nargs="*", default=[],
                    help="extra strings to trace (e.g. cell refs E132 E25)")
    args = ap.parse_args(argv)

    with zipfile.ZipFile(args.workbook) as z:
        parts = {n: z.read(n) for n in z.namelist()}

    print(f"workbook: {args.workbook}")
    print(f"{len(parts)} zip parts:")
    for n in sorted(parts):
        print(f"  {n}")
    non_standard = [n for n in parts if not n.startswith(_STANDARD_PREFIXES)]
    print(f"\nNON-STANDARD parts (prime registry suspects): "
          f"{non_standard or '(none)'}")
    hinted = sorted({n for n in parts
                     if any(h.lower() in n.lower() for h in _REGISTRY_HINTS)})
    print(f"registry-hint parts (vba/customXml/ctrlProp/etc.): "
          f"{hinted or '(none)'}")

    for needle in list(args.keys) + list(args.terms):
        print(f"\n=== references to {needle!r} ===")
        appears_in = []
        for n in sorted(parts):
            for ctx in _search(parts[n], needle):
                print(f"  [{n}] …{ctx}…")
                if n not in appears_in:
                    appears_in.append(n)
        print(f"  -> appears in {len(appears_in)} part(s): {appears_in}")

    # The diff that matters: parts that reference the FIRST key but not others.
    if len(args.keys) >= 2:
        sets = {k: {n for n in parts if _search(parts[n], k)}
                for k in args.keys}
        base = args.keys[0]
        for other in args.keys[1:]:
            only = sorted(sets[base] - sets[other])
            print(f"\nparts referencing {base} but NOT {other} "
                  f"(the missing artifact?): {only or '(none — shapes match)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
