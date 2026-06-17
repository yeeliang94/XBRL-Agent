"""Concept-tree bootstrap — import every face template into the live DB.

``concept_nodes`` is global/per-template (no ``run_id``), so the canonical
Concepts UI can only render a tree for templates that have been imported. A
live run never imports them itself, so this module does it once at server
startup (and is exposed as ``python -m concept_model.bootstrap`` for CLI/test).

Walks the variant registry (``statement_types.VARIANTS``) across both filing
standards and both filing levels, parsing each template to a concept tree and
upserting it via :func:`concept_model.importer.import_template`. Every linear
template additionally gets per-scope ``concept_targets`` so the exporter routes
each fact via a single keyed lookup (rewrite Phase 6.1): Company linear via
:func:`import_company_targets` (B=CY, C=PY), Group linear via
:func:`import_group_targets` (B/C/D/E). Matrix (SOCIE) templates carry their
targets inline so the importer writes them directly.

Idempotent — the importer's deterministic UUID5 keys make re-imports a no-op,
so calling this on every startup is safe and cheap.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from statement_types import VARIANTS, template_path
from notes_types import NOTES_REGISTRY, notes_template_path
from concept_model.importer import (
    import_company_targets,
    import_group_targets,
    import_template,
)
from concept_model.notes_importer import import_notes_template
from concept_model.notes_parser import parse_notes_template
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


def import_all_notes_templates(db_path: str | Path) -> list[str]:
    """Import every notes template into ``db_path``. Returns the template_ids.

    Two tracks (PLAN-notes-template-registry):
      * PROSE notes (Corporate Info, Accounting Policies, List of Notes) →
        the ``notes_nodes`` registry (HTML cells live in ``notes_cells``).
      * NUMERIC notes (Issued Capital, Related Party) → the existing
        ``concept_model`` pipeline via :func:`_import_one`, exactly like a face
        statement — they are multi-column numeric tables, not prose.

    Slot numbering differs by standard (MFRS 10..14, MPERS 11..15); that's
    handled inside :func:`notes_template_path`. Idempotent like the face
    bootstrap — re-imports are no-ops thanks to deterministic ids.
    """
    template_ids: list[str] = []
    for template_type, entry in NOTES_REGISTRY.items():
        for standard in _STANDARDS:
            for level in _LEVELS:
                try:
                    path = notes_template_path(template_type, level, standard)
                except ValueError:
                    continue
                if not path.exists():
                    logger.warning(
                        "bootstrap: notes template missing on disk: %s", path
                    )
                    continue
                if entry.is_numeric:
                    # Track B: numeric notes reuse the concept_model pipeline.
                    template_id = _import_one(db_path, path, level)
                else:
                    # Track A: prose notes → the notes_nodes registry.
                    template_id, nodes = parse_notes_template(
                        str(path), entry.sheet_name
                    )
                    import_notes_template(db_path, template_id, nodes)
                template_ids.append(template_id)
    return template_ids


def _import_one(db_path: str | Path, template_xlsx: Path, level: str) -> str:
    """Parse one template xlsx, import it, and fill per-scope targets.

    Every linear template gets ``concept_targets`` rows so the exporter
    routes each fact via a single keyed lookup (Phase 6.1); matrix (SOCIE)
    templates carry their per-cell targets inline from ``import_template``.
    """
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
    # already wrote them. Linear templates get per-scope target rows so the
    # exporter routes via a single concept_targets lookup: Group needs the
    # B/C/D/E Company-vs-Group columns; Company needs the B=CY/C=PY pair.
    if payload.get("shape", "linear") != "matrix":
        if level == "group":
            import_group_targets(db_path, template_id)
        else:
            import_company_targets(db_path, template_id)
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
