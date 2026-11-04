"""Build a source-only zip for a Veracode static scan.

Usage: venv/bin/python scripts/package_for_veracode.py [--include-tests] [--out PATH]

Rules:
- Start from the files git tracks, which already skips everything in
  .gitignore: venv, node_modules, dist, output/, data/, logs, .env, *.db.
  Untracked files are listed and refused, so a loose export in the checkout
  never reaches the upload; commit, delete or gitignore them first.
- Drop data folders that hold no code: the SSM taxonomy, the Excel
  templates, docs, and binary files (xlsx, pdf, docx, images).
- Drop tests by default (they are not shipped and add false positives).
- Refuse any .env* file except .env.example, as a last line of defence.
- Keep requirements.txt, constraints.txt, web/package.json and
  web/package-lock.json so Veracode SCA can read the dependency list.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.paths import operator_path  # noqa: E402

import argparse
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = (
    "SSMxT_2022v1.0",
    "XBRL-template-MFRS",
    "XBRL-template-MPERS",
    "docs",
    ".claude",
    ".github",
    "veracode",
)
EXCLUDED_SUFFIXES = (
    ".xlsx", ".xlsm", ".xls", ".pdf", ".docx", ".doc",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
    ".db", ".log", ".zip", ".pyc", ".DS_Store",
)
EXCLUDED_NAMES = (".gitignore", ".gitattributes", ".DS_Store", "greptile.json")


def git_files(*extra: str) -> list[str]:
    cmd = ["git", "ls-files", "-z", *extra]
    out = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def keep(rel: str, include_tests: bool) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if name.startswith(".env") and name != ".env.example":
        return False
    if parts[0] in EXCLUDED_DIRS:
        return False
    if not include_tests and parts[0] == "tests":
        return False
    if "__pycache__" in parts or "node_modules" in parts or "dist" in parts:
        return False
    if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
        return False
    return (ROOT / rel).is_file()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    loose = sorted(f for f in git_files("--others", "--exclude-standard") if keep(f, args.include_tests))
    if loose:
        print("ERROR: untracked files would be uploaded. Commit, delete or "
              "gitignore them first:", file=sys.stderr)
        for rel in loose:
            print(f"  {rel}", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = ROOT / "veracode"
    operator_path(out_dir).mkdir(exist_ok=True)
    out = Path(args.out) if args.out else out_dir / f"xbrl-agent-veracode-{stamp}.zip"

    files = sorted(f for f in git_files("--cached") if keep(f, args.include_tests))
    if not files:
        print("ERROR: no files selected; is this a git checkout?", file=sys.stderr)
        return 1

    with zipfile.ZipFile(operator_path(out), "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(ROOT / rel, rel)

    by_ext: dict[str, int] = {}
    for rel in files:
        ext = os.path.splitext(rel)[1] or "(none)"
        by_ext[ext] = by_ext.get(ext, 0) + 1
    print(f"Wrote {out}")
    print(f"  {len(files)} files, {out.stat().st_size / 1_048_576:.1f} MB")
    for ext, n in sorted(by_ext.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {ext}")
    print("  tests included" if args.include_tests else "  tests excluded (use --include-tests to add them)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
