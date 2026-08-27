#!/usr/bin/env python3
"""Read-only audit of canonical writable fields across active templates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "concept_model/template_field_semantics_audit.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from concept_model.filing_targets import audit_active_templates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit taxonomy capability and workbook slot writability."
    )
    parser.add_argument(
        "--write-snapshot",
        action="store_true",
        help="Refresh the committed machine-readable snapshot.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when live templates differ from the snapshot.",
    )
    args = parser.parse_args()

    result = audit_active_templates(ROOT)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.write_snapshot:
        SNAPSHOT.write_text(rendered, encoding="utf-8")
    if args.check:
        if not SNAPSHOT.exists() or SNAPSHOT.read_text(encoding="utf-8") != rendered:
            print("Template field-semantics snapshot is stale.", file=sys.stderr)
            return 1
        print(
            "Template field semantics verified: "
            f"{result['templates']} workbooks, {result['worksheets']} sheets, "
            f"{result['numeric_slots']} numeric and {result['prose_slots']} prose slots."
        )
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
