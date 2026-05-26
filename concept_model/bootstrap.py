"""Concept-tree bootstrap — import every face template into the live DB.

``concept_nodes`` is global/per-template (no ``run_id``), so the canonical
Concepts UI can only render a tree for templates that have been imported. A
live run never imports them itself, so this module does it once at server
startup (and is exposed as ``python -m concept_model.bootstrap`` for CLI/test).

Walks the variant registry (``statement_types.VARIANTS``) across both filing
standards and both filing levels, parsing each template to a concept tree and
upserting it via :func:`concept_model.importer.import_template`. Group *linear*
templates additionally get per-scope ``concept_targets`` via
:func:`import_group_targets`; matrix (SOCIE) templates carry their targets
inline so the importer writes them directly.

Idempotent — the importer's deterministic UUID5 keys make re-imports a no-op,
so calling this on every startup is safe and cheap.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from statement_types import VARIANTS, template_path
from concept_model.importer import import_group_targets, import_template
from concept_model.parser import parse_template

logger = logging.getLogger(__name__)

_STANDARDS = ("mfrs", "mpers")
_LEVELS = ("company", "group")


def import_all_face_templates(db_path: str | Path) -> list[str]:
    """Import every face template into ``db_path``. Returns the template_ids.

    Skips (statement, variant) combinations with no template file
    (e.g. SOCI/NotPrepared) and standard/variant mismatches (e.g. SoRE on
    MFRS) — both surface as ``ValueError`` from :func:`template_path`.
    """
    template_ids: list[str] = []
    for (statement, variant_name) in VARIANTS:
        for standard in _STANDARDS:
            for level in _LEVELS:
                try:
                    path = template_path(statement, variant_name, level, standard)
                except ValueError:
                    # No template (NotPrepared) or standard/variant mismatch.
                    continue
                if not path.exists():
                    logger.warning("bootstrap: template missing on disk: %s", path)
                    continue
                template_id = _import_one(db_path, path, level)
                template_ids.append(template_id)
    return template_ids


def _import_one(db_path: str | Path, template_xlsx: Path, level: str) -> str:
    """Parse one template xlsx, import it, and (for Group linear) fill targets."""
    tree = parse_template(str(template_xlsx))
    payload = tree.to_json()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh, sort_keys=True)
        json_path = fh.name
    try:
        template_id = import_template(db_path, json_path)
    finally:
        Path(json_path).unlink(missing_ok=True)

    # Matrix (SOCIE) templates carry per-cell targets inline; the importer
    # already wrote them. Group *linear* templates need the B/C/D/E per-scope
    # target rows so the exporter can route Company vs Group columns.
    if level == "group" and payload.get("shape", "linear") != "matrix":
        import_group_targets(db_path, template_id)
    return template_id


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Import all face templates into the canonical concept DB."
    )
    ap.add_argument("db_path", help="Path to the audit/canonical SQLite DB")
    args = ap.parse_args()

    ids = import_all_face_templates(args.db_path)
    print(f"Imported {len(ids)} face templates into {args.db_path}")


if __name__ == "__main__":
    main()
