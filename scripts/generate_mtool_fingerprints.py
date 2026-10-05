"""Register the mTool templates whose layout we have actually inspected.

Step 18 of docs/PLAN-mtool-fill-pipeline.md (peer-review finding 7). Column
detection is allowed to proceed unattended only on a template whose structural
fingerprint is on file; anything else needs a human to confirm the layout
(Step 10). This script is what puts a template "on file".

Each entry records PROVENANCE — where the file came from and who vouched for
it — because "we've seen this shape" is a claim someone has to have made. A
fingerprint with no provenance would just be a cache of whatever was uploaded
first.

Run: ``python3 scripts/generate_mtool_fingerprints.py`` — rewrites
``mtool/known_templates.json``. Re-run after regenerating any template.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from mtool.column_detect import fingerprint_workbook  # noqa: E402
from mtool.offline_fill import load_workbook_entries  # noqa: E402

_OUTPUT = _REPO_ROOT / "mtool" / "known_templates.json"

# (path, name, standard, level, source, layout, vouched_by)
#
# `source` says where the file came from — the two kinds behave very
# differently and the distinction is the whole point of finding 7:
#   * "generated"  — our own template, produced from the SSM linkbase by this
#                    repo. Labels are the same vocabulary but the physical
#                    layout (labels col A, values B/C) is OURS, not mTool's.
#   * "ssm-mtool"  — a real SSM-issued mTool workbook. These carry mTool's
#                    marker rows, so detection reads them semantically.
_CORPUS: list[tuple[str, str, str, str, str, str, str]] = []

for _std, _dir in (("mfrs", "XBRL-template-MFRS"), ("mpers", "XBRL-template-MPERS")):
    for _level in ("Company", "Group"):
        _folder = _REPO_ROOT / _dir / _level
        for _path in sorted(_folder.glob("*.xlsx")):
            _CORPUS.append((
                str(_path.relative_to(_REPO_ROOT)),
                _path.stem,
                _std,
                _level.lower(),
                "generated",
                "ours-A-labels-BC-values",
                "repo (generated from the SSM linkbase)",
            ))

_CORPUS.append((
    "data/MBRS_test.xlsx",
    "SSM mTool MFRS Company (2024 sample)",
    "mfrs",
    "company",
    "ssm-mtool",
    "mtool-D-labels-EF-values",
    "Windows operator, 2026-07-04 spike (Validate + Generate accepted)",
))


def main() -> int:
    registry: dict[str, dict] = {}
    skipped: list[str] = []
    for (rel, name, standard, level, source, layout, vouched) in _CORPUS:
        path = _REPO_ROOT / rel
        if not path.exists():
            skipped.append(rel)
            continue
        _, data, _ = load_workbook_entries(str(path))
        fp = fingerprint_workbook(data)
        # A collision is information, not an error: two files with the same
        # layout SHOULD share a fingerprint (MFRS and MPERS versions of the
        # same statement are laid out identically). The entry therefore records
        # the SET of standards/levels it covers rather than claiming one —
        # a fingerprint answers "have we vouched for this shape", not "which
        # filing is this".
        entry = registry.setdefault(fp, {
            "name": name, "filing_standards": [], "filing_levels": [],
            "source": source, "layout": layout, "vouched_by": vouched,
            "examples": [],
        })
        entry["examples"].append(rel)
        if standard not in entry["filing_standards"]:
            entry["filing_standards"].append(standard)
        if level not in entry["filing_levels"]:
            entry["filing_levels"].append(level)

    _OUTPUT.write_text(
        json.dumps(registry, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(registry)} distinct layouts from "
          f"{len(_CORPUS) - len(skipped)} files -> {_OUTPUT.name}")
    for fp, entry in sorted(registry.items()):
        print(f"  {fp}  {entry['source']:10s} {len(entry['examples']):2d} file(s)"
              f"  {entry['name']}")
    for rel in skipped:
        print(f"  MISSING (not registered): {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
