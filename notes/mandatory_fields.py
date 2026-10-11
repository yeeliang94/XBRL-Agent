"""Required policy disclosure rules shared by extraction and review."""
import json
import re
from typing import TYPE_CHECKING

from notes.html_to_text import html_to_excel_text

if TYPE_CHECKING:
    from notes.agent import NotesDeps

DISPOSITION_PREFIX = "mandatory-field:"


def is_required_policy(label: str) -> bool:
    return label.startswith("*") or label.lstrip("*").strip().lower() in {
        "disclosure of material accounting policy information",
        "disclosure of significant accounting policies",
    }


def policy_has_content(html: str) -> bool:
    body = re.sub(r"<h[1-6]\b[^>]*>.*?</h[1-6]>", "", html, flags=re.IGNORECASE | re.DOTALL)
    return bool(html_to_excel_text(body).strip())


def record_missing_policy(deps: "NotesDeps", *, row: int, target_label: str,
                          source_pages: list[int], reason: str, evidence: str) -> str:
    """Persist a grounded disposition without changing the required field."""
    from db import repository as repo

    if not deps.db_path or deps.run_id is None:
        return "rejected: a persisted run is required for human-review reporting."
    if (not source_pages or not set(source_pages).issubset(deps.viewed_pdf_pages)
            or not reason.strip() or not evidence.strip()):
        return "rejected: view the supporting PDF pages and supply a reason and source evidence."
    with deps.io_lock, repo.db_session(deps.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        nodes = repo.list_notes_node_rows(conn, sheet=deps.sheet_name,
            template_prefix=f"{deps.filing_standard}-{deps.filing_level}-")
        if not any(n["row"] == row and n["label"] == target_label
                   and n["kind"] == "LEAF" and is_required_policy(target_label) for n in nodes):
            return "rejected: choose a required policy field's exact live row and label."
        cell = conn.execute("SELECT html FROM notes_cells WHERE run_id=? AND sheet=? AND row=?",
            (deps.run_id, deps.sheet_name, row)).fetchone()
        if cell and policy_has_content(cell[0] or ""):
            return "rejected: this field already contains substantive disclosure."
        finding = f"{DISPOSITION_PREFIX}{deps.sheet_name}:{row}"
        pages = sorted(set(source_pages))
        existing = conn.execute("SELECT id FROM notes_review_flags WHERE run_id=? AND finding_id=? AND status='open'",
            (deps.run_id, finding)).fetchone()
        if existing:
            conn.execute("UPDATE notes_review_flags SET reason=?, source_pages=?, evidence=? WHERE id=?",
                (reason.strip(), json.dumps(pages), evidence.strip(), existing[0]))
        else:
            repo.insert_notes_review_flag(conn, run_id=deps.run_id,
                kind="needs_human", reason=reason.strip(), sheet=deps.sheet_name, row=row,
                finding_id=finding, source_pages=pages, evidence=evidence.strip())
    warning = f"Required field {target_label!r} (row {row}) needs human review: {reason.strip()}"
    if warning not in deps.write_skip_errors:
        deps.write_skip_errors.append(warning)
    return "Recorded missing required disclosure for human review; the field remains unresolved."
