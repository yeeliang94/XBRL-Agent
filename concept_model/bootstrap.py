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

Idempotent — the importer's deterministic UUID5 keys make re-imports a no-op.
A template is written again only when what it would write has changed
(``template_imports``, schema v58): the fingerprint covers the parser's output
for the template, its filing manifest and the code that writes the rows. On
Azure SQL a full import is tens of thousands of round trips, so an unchanged
start must not repeat it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator

from db import engine as db_engine
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
from concept_model.filing_targets import persist_template_manifest, targets_for_template

logger = logging.getLogger(__name__)

_STANDARDS = ("mfrs", "mpers")
_LEVELS = ("company", "group")
_ROOT = Path(__file__).resolve().parent.parent
# Code that turns parsed templates into rows. What it is given is fingerprinted
# from the parser's output, so only the writers' own source counts here.
_WRITERS = ("concept_model/bootstrap.py", "concept_model/importer.py",
            "concept_model/notes_importer.py", "concept_model/filing_targets.py",
            "reports/registry.py")
_LOCK = "xbrl-template-bootstrap"


def import_all_face_templates(db_path: str | Path) -> list[str]:
    """Import every face template into ``db_path``. Returns the template_ids.

    Skips (statement, variant) combinations with no template file
    (e.g. SOCI/NotPrepared) and standard/variant mismatches (e.g. SoRE on
    MFRS) — both surface as ``ValueError`` from :func:`template_path`.
    """
    jobs = []
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
                jobs.append(_face_job(db_path, path, level))
    return _run(db_path, "face", jobs)


def import_all_notes_templates(db_path: str | Path) -> list[str]:
    """Import every notes template into ``db_path``. Returns the template_ids.

    Two tracks (PLAN-notes-template-registry):
      * PROSE notes (Corporate Info, Accounting Policies, List of Notes) →
        the ``notes_nodes`` registry (HTML cells live in ``notes_cells``).
      * NUMERIC notes (Issued Capital, Related Party) → the existing
        ``concept_model`` pipeline via :func:`_import_one`, exactly like a face
        statement — they are multi-column numeric tables, not prose.

    Slot numbering differs by standard (MFRS 10..14, MPERS 11..15); that's
    handled inside :func:`notes_template_path`. The report templates
    (directors' report and the like) follow; their ids are not returned.
    """
    jobs = []
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
                    jobs.append(_face_job(db_path, path, level))
                else:
                    # Track A: prose notes → the notes_nodes registry.
                    jobs.append(_prose_job(db_path, path, entry.sheet_name))
    template_ids = _run(db_path, "notes", jobs)
    import_report_templates(db_path)
    return template_ids


def import_report_templates(db_path: str | Path) -> list[str]:
    """Import the report templates (directors' report and the like) as prose notes."""
    from reports.registry import report_templates

    return _run(db_path, "report", [_prose_job(db_path, path, sheet, keep)
                                    for path, sheet, keep in report_templates()])


def _import_one(db_path: str | Path, template_xlsx: Path, level: str) -> str:
    """Parse one template xlsx, import it, and fill per-scope targets."""
    _path, (_template_id, _parsed, write) = _face_job(db_path, template_xlsx, level)
    return write()


# A job is (template_id, what the parser produced, a function that writes it).
_Job = tuple[str, Any, Callable[[], str]]


def _face_job(db_path: str | Path, template_xlsx: Path, level: str) -> tuple[Path, _Job]:
    payload = parse_template(str(template_xlsx)).to_json()

    def write() -> str:
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
        persist_template_manifest(db_path, template_xlsx, force=True)
        return template_id

    return template_xlsx, (payload["template_id"], [level, payload], write)


def _prose_job(db_path: str | Path, path: Path, sheet: str,
               keep: Callable[[Any], bool] | None = None) -> tuple[Path, _Job]:
    template_id, nodes = parse_notes_template(str(path), sheet)
    if keep is not None:
        nodes = [n for n in nodes if keep(n)]

    def write() -> str:
        import_notes_template(db_path, template_id, nodes)
        persist_template_manifest(db_path, path, force=True)
        return template_id

    return path, (template_id, nodes, write)


def _run(db_path: str | Path, label: str, jobs: list[tuple[Path, _Job]]) -> list[str]:
    started = time.monotonic()
    imported = 0
    with _bootstrap_lock(db_path):
        for path, (template_id, parsed, write) in jobs:
            imported += _write_if_changed(db_path, path, template_id, parsed, write)
    logger.info("bootstrap: %d %s templates, %d imported, %d unchanged, %.1fs",
                len(jobs), label, imported, len(jobs) - imported, time.monotonic() - started)
    return [template_id for _path, (template_id, _parsed, _write) in jobs]


@lru_cache(maxsize=1)
def _writer_code() -> bytes:
    return b"".join(name.encode() + b"\0" + (_ROOT / name).read_bytes() for name in _WRITERS)


def _fingerprint(path: Path, parsed: Any) -> str:
    _template_id, targets = targets_for_template(path)
    digest = hashlib.sha256(_writer_code())
    digest.update(json.dumps(parsed, sort_keys=True, default=repr).encode())
    digest.update(repr(targets).encode())
    return digest.hexdigest()


def _write_if_changed(db_path: str | Path, path: Path, template_id: str, parsed: Any,
                      write: Callable[[], str]) -> bool:
    """Write one template unless the same rows were already written. True if written.

    The stored fingerprint is removed before writing and stored again only
    after every write succeeded, so an interrupted import is redone.
    """
    path = path.resolve()
    try:
        key = path.relative_to(_ROOT).as_posix()
    except ValueError:
        key = path.as_posix()
    fingerprint = _fingerprint(path, parsed)
    conn = db_engine.connect(str(db_path))
    try:
        # The template's rows must still be there, not only the record of them.
        if conn.execute(
                "SELECT 1 FROM template_imports t WHERE t.source_path = ? AND t.fingerprint = ? "
                "AND t.template_id = ? AND (EXISTS (SELECT 1 FROM concept_templates c "
                "WHERE c.template_id = t.template_id) OR EXISTS (SELECT 1 FROM notes_nodes n "
                "WHERE n.template_id = t.template_id))",
                (key, fingerprint, template_id)).fetchone():
            return False
        conn.execute("DELETE FROM template_imports WHERE source_path = ?", (key,))
        conn.commit()
    finally:
        conn.close()
    write()
    conn = db_engine.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO template_imports(source_path, template_id, fingerprint, imported_at) "
            "VALUES (?, ?, ?, ?)",
            (key, template_id, fingerprint, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return True


@contextmanager
def _bootstrap_lock(db_path: str | Path) -> Iterator[None]:
    """One server at a time checks and writes templates.

    During a deploy the old and new servers can start against the same Azure
    SQL database. Without this, one could record its fingerprint over rows the
    other has just written, and a later start would skip a needed import.
    SQLite is never shared between servers, so it needs no lock.
    """
    conn = db_engine.connect(str(db_path))
    try:
        if getattr(conn, "dialect", None) != "mssql":
            yield
            return
        from db import mssql

        held = mssql.hold_session_lock(conn, _LOCK, timeout_ms=30 * 60 * 1000)
    finally:
        conn.close()
    try:
        yield
    finally:
        mssql.release_session_lock(held, _LOCK)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description="Import all face templates into the canonical concept DB."
    )
    ap.add_argument("db_path", help="Path to the audit/canonical SQLite DB")
    args = ap.parse_args()

    from utils.paths import approve_operator_folder, operator_path

    approve_operator_folder(operator_path(args.db_path).parent)
    ids = import_all_face_templates(args.db_path)
    print(f"Imported {len(ids)} face templates into {args.db_path}")


if __name__ == "__main__":
    main()
