"""Create an isolated draft from a prior run for a full redo.

The parent run is immutable.  A restart gets a new session directory and a
new ``runs`` row, while source preparation is copied only when its hashes,
contract version, model configuration, and completed document map still
validate.  Callers can then use the ordinary draft-edit and start interfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import uuid

from db import repository as repo
from ingest.document_preparation import read_prepared_document
from recovery.stage_resume import compute_source_sha256
from utils.atomic_io import write_json_atomic


class RunRestartError(RuntimeError):
    """A prior run cannot be cloned safely."""


@dataclass(frozen=True)
class RestartDraft:
    run_id: int
    session_id: str
    preparation_reused: bool
    preparation: dict | None


def _read_reusable_preparation(
    source_dir: Path,
    *,
    model_name: str,
    configuration_key: str,
) -> tuple[dict, list[Path]] | None:
    """Return the validated readiness snapshot and its owned files."""
    prepared = read_prepared_document(
        source_dir / "uploaded.pdf",
        model_name=model_name,
        configuration_key=configuration_key,
    )
    if prepared is None:
        return None
    try:
        status = json.loads(
            (source_dir / "preparation_status.json").read_text(encoding="utf-8")
        )
        metadata = json.loads(
            (source_dir / "preparation.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return None
    if (
        status.get("status") != "succeeded"
        or not isinstance(status.get("infopack"), dict)
        or metadata.get("inventory_reconciled") is not True
    ):
        return None

    required_names = {
        "preparation.json",
        "source.html",
        "source_meta.json",
        *(
            str(metadata[name])
            for name in ("html_file", "pdf_file", "native_html_file")
            if metadata.get(name)
        ),
    }
    if not all((source_dir / name).is_file() for name in required_names):
        return None
    files = [
        path for path in source_dir.iterdir()
        if path.is_file()
        and (
            path.name in required_names
            or path.name.startswith("preparation-checkpoint-")
        )
    ]
    return status, files


def _copy_source_files(source_dir: Path, target_dir: Path) -> None:
    for name in ("uploaded.pdf", "uploaded.docx", "original_filename.txt"):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, target_dir / name)
    # Word-native HTML is an input derived from uploaded.docx, independent of
    # the model-built PDF preparation.  Preserve it even when preparation must
    # be rebuilt.  PDF transcriptions travel only with a valid preparation.
    if (source_dir / "uploaded.docx").is_file():
        source_html = source_dir / "source.html"
        if source_html.is_file():
            shutil.copy2(source_html, target_dir / source_html.name)


def clone_run_as_draft(
    db_path: str | Path,
    output_root: str | Path,
    parent_run_id: int,
    *,
    model_name: str,
    configuration_key: str,
) -> RestartDraft:
    """Clone one non-running run into a new editable draft.

    This is the module's interface.  It owns filesystem isolation, preparation
    validation, draft creation, and lineage persistence.  It never starts an
    extraction or a paid model request.
    """
    db_path = Path(db_path)
    output_root = Path(output_root)
    with repo.db_session(db_path) as conn:
        parent = repo.fetch_run(conn, parent_run_id)
    if parent is None:
        raise RunRestartError("Run not found")
    if parent.status == "running":
        raise RunRestartError(
            "This run is still running. Wait for it to finish or abort it before redoing it."
        )
    if not parent.output_dir:
        raise RunRestartError("This run has no retained source document to redo.")

    source_dir = Path(parent.output_dir)
    source_pdf = source_dir / "uploaded.pdf"
    if not source_pdf.is_file():
        raise RunRestartError(
            "The source document for this run is no longer available. Upload it again."
        )

    reusable = _read_reusable_preparation(
        source_dir,
        model_name=model_name,
        configuration_key=configuration_key,
    )
    session_id = str(uuid.uuid4())
    target_dir = output_root / session_id
    target_dir.mkdir(parents=True, exist_ok=False)

    child_run_id: int | None = None
    try:
        _copy_source_files(source_dir, target_dir)
        if not (target_dir / "uploaded.pdf").is_file():
            raise RunRestartError("The source document could not be copied.")

        # The map's infopack is part of the saved run configuration.  Backfill
        # it for older runs whose config was persisted before preparation
        # completed, so the cloned setup shows the same document map.
        config = dict(parent.config or {})
        if reusable is not None and not isinstance(config.get("infopack"), dict):
            config["infopack"] = reusable[0]["infopack"]

        with repo.db_session(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            child_run_id = repo.create_run(
                conn,
                pdf_filename=parent.pdf_filename,
                session_id=session_id,
                output_dir=str(target_dir),
                config=config,
                scout_enabled=bool(parent.scout_enabled),
                status="draft",
            )

            preparation_snapshot = None
            if reusable is not None:
                prior_status, preparation_files = reusable
                for source in preparation_files:
                    shutil.copy2(source, target_dir / source.name)
                # Validate the copied bundle, not only the parent bundle.
                copied = read_prepared_document(
                    target_dir / "uploaded.pdf",
                    model_name=model_name,
                    configuration_key=configuration_key,
                )
                if copied is None:
                    raise RunRestartError(
                        "The saved document preparation could not be copied safely."
                    )
                preparation_snapshot = {
                    **prior_status,
                    "run_id": child_run_id,
                    "reuse_from_run_id": parent_run_id,
                    "message": "Document preparation and map reused from the prior run",
                }
                write_json_atomic(
                    target_dir / "preparation_status.json",
                    preparation_snapshot,
                )

            statements = [str(value) for value in config.get("statements") or []]
            repo.create_run_lineage(
                conn,
                child_run_id=child_run_id,
                parent_run_id=parent_run_id,
                source_sha256=compute_source_sha256(source_pdf),
                reused_statements=[],
                rerun_statements=statements,
            )

        return RestartDraft(
            run_id=child_run_id,
            session_id=session_id,
            preparation_reused=reusable is not None,
            preparation=preparation_snapshot,
        )
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
