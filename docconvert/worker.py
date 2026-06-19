"""Background conversion worker for the scanned-PDF → readable-doc feature.

A conversion runs on a plain daemon thread (the Docling pipeline is synchronous
CPU work — no event loop needed, unlike the reviewer pass). The worker owns its
own SQLite connection and commits progress after every page so the status/SSE
readers (separate connections, WAL mode) see live updates. Every exit path —
success, bad PDF, timeout, crash — lands the job in a terminal state, so a poll
always resolves (terminal-status contract, CLAUDE.md gotcha #10).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from db import repository as repo
from .converter import convert_pdf_to_html, DocConvertError

logger = logging.getLogger(__name__)

# Defence-in-depth wall-clock cap (seconds). A slow/huge PDF that crawls page by
# page is failed rather than tying up the single conversion slot forever.
# Override via XBRL_DOC_CONVERT_TIMEOUT_S; 0 disables. Checked between pages.
_DEFAULT_TIMEOUT_S = 600.0


def _timeout_s() -> float:
    try:
        return float(os.environ.get("XBRL_DOC_CONVERT_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def run_conversion_job(
    db_path: str | Path,
    job_id: int,
    pdf_path: str | Path,
    result_html_path: str | Path,
    *,
    models_dir: str | Path | None = None,
) -> None:
    """Convert one PDF and persist the outcome. Safe to run on a daemon thread.

    Reads/writes only the `doc_conversions` row for ``job_id``. Never raises —
    failures are recorded on the job so the caller thread can die quietly.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    started = time.monotonic()
    cap = _timeout_s()

    try:
        repo.update_doc_conversion_progress(
            conn, job_id, current_page=0, total_pages=0, status="running"
        )
        conn.commit()

        def _progress(done: int, total: int) -> None:
            # Enforce the wall-clock cap at each page boundary.
            if cap > 0 and (time.monotonic() - started) > cap:
                raise DocConvertError(
                    "Conversion took too long and was stopped. Try a smaller PDF."
                )
            repo.update_doc_conversion_progress(
                conn, job_id, current_page=done, total_pages=total
            )
            conn.commit()

        html = convert_pdf_to_html(
            pdf_path, model_dir=models_dir, progress_cb=_progress
        )
        out = Path(result_html_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")  # encoding explicit (gotcha #1)

        repo.mark_doc_conversion_finished(
            conn, job_id, status="done", result_html_path=str(out)
        )
        conn.commit()
        logger.info("doc-conversion %s completed", job_id)
    except DocConvertError as exc:
        # User-actionable failure (bad/password/empty PDF, timeout, missing
        # models) — the message is safe to show directly.
        repo.mark_doc_conversion_finished(conn, job_id, status="failed", error=str(exc))
        conn.commit()
        logger.info("doc-conversion %s failed: %s", job_id, exc)
    except Exception as exc:  # noqa: BLE001 - never let the worker thread escape
        repo.mark_doc_conversion_finished(
            conn,
            job_id,
            status="failed",
            error="Conversion failed unexpectedly. Try a smaller file or "
            "contact support.",
        )
        conn.commit()
        logger.warning("doc-conversion %s crashed: %s", job_id, exc, exc_info=True)
    finally:
        conn.close()
