"""Automatic PDF-notes formatting after extraction and notes review.

PDF content is persisted structure-first. This module runs the existing
style-only formatter over unstyled prose cells, in parallel by sheet, and
persists the same task rows the manual Notes-tab action uses. Word runs are
excluded by the server caller; their source-formatting contract is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded

from db import repository as repo
from notes.formatting_agent import run_notes_formatter

logger = logging.getLogger(__name__)

PDF_FORMAT_CANDIDATE_SOURCES = {"unstyled", "floor"}


def candidate_sheets(
    db_path: str, run_id: int, requested_sheets: Iterable[str],
) -> list[str]:
    """Return requested sheets with at least one unstyled prose cell."""
    requested = set(requested_sheets)
    with repo.db_session(db_path) as conn:
        cells = repo.list_notes_cells_for_run(conn, run_id)
    return sorted({
        c.sheet for c in cells
        if c.sheet in requested
        and (c.html or "").strip()
        and c.style_source in PDF_FORMAT_CANDIDATE_SOURCES
    })


def _persist_outcome(
    db_path: str, run_id: int, sheet: str, model_name: str,
    result: dict[str, Any],
) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_format_task(
            conn, run_id, sheet, "done", model=model_name,
            summary=result.get("summary"),
            confidence=result.get("confidence"),
            changed_rows=int(result.get("changed_rows") or 0),
            result=result, error=result.get("error"),
            error_type=result.get("error_type"),
            before_text_hash=result.get("before_text_hash"),
            after_text_hash=result.get("after_text_hash"),
            prompt_tokens=int(result.get("prompt_tokens") or 0),
            completion_tokens=int(result.get("completion_tokens") or 0),
            cache_read_tokens=int(result.get("cache_read_tokens") or 0),
            cache_write_tokens=int(result.get("cache_write_tokens") or 0),
        )


async def run_pdf_auto_format(
    *,
    run_id: int,
    db_path: str,
    pdf_path: str,
    sheets: Iterable[str],
    model_name: str,
    model_factory: Callable[[], Any],
    output_dir: str,
    timeout_s: float,
    formatter=run_notes_formatter,
    on_progress: Callable[[int, int, str | None], None] | None = None,
) -> dict[str, Any]:
    """Format eligible PDF-note sheets and return an advisory summary.

    Sheet failures are isolated and persisted. Cancellation is propagated so
    Stop All retains its run-level meaning.
    """
    selected = candidate_sheets(db_path, run_id, sheets)
    if not selected:
        return {"sheets": {}, "formatted": 0, "failed": 0, "skipped": 0}
    if on_progress is not None:
        try:
            on_progress(0, len(selected), None)
        except Exception:  # noqa: BLE001 — display progress is advisory
            logger.warning(
                "automatic PDF formatter progress callback failed",
                exc_info=True,
            )

    async def one(sheet: str) -> tuple[str, dict[str, Any]]:
        try:
            with repo.db_session(db_path) as conn:
                claim = repo.claim_notes_format_task_guarded(
                    conn, run_id, sheet, model=model_name,
                )
            if claim == "reviewer_running":
                result = {
                    "ok": False,
                    "skipped": True,
                    "error_type": "reviewer_running",
                    "error": "A notes reviewer pass is already running.",
                    "summary": (
                        "Automatic formatting was skipped because notes review "
                        "was still running."
                    ),
                }
                logger.info(
                    "automatic PDF formatter skipped behind notes reviewer "
                    "run=%s sheet=%s",
                    run_id, sheet,
                )
            elif claim == "format_running":
                # Never replace another pass's durable running row with this
                # launch's terminal outcome. The existing owner will finish it.
                return sheet, {
                    "ok": False,
                    "skipped": True,
                    "error_type": "format_running",
                    "error": "A notes formatter pass is already running.",
                    "summary": "Automatic formatting was already in progress.",
                }
            else:
                coro = formatter(
                    run_id=run_id, db_path=db_path, pdf_path=pdf_path,
                    sheet=sheet, model=model_factory(), output_dir=output_dir,
                    style_sources=PDF_FORMAT_CANDIDATE_SOURCES,
                )
                result = (
                    await asyncio.wait_for(coro, timeout=timeout_s)
                    if timeout_s and timeout_s != float("inf")
                    else await coro
                )
        except asyncio.CancelledError:
            result = {
                "ok": False, "error_type": "cancelled",
                "error": "Automatic PDF formatting was cancelled.",
                "summary": "Formatting was cancelled; no new changes were saved.",
            }
            try:
                _persist_outcome(db_path, run_id, sheet, model_name, result)
            except Exception:  # noqa: BLE001 — cancellation must still propagate
                logger.warning(
                    "could not persist cancelled PDF formatter task "
                    "run=%s sheet=%s",
                    run_id, sheet, exc_info=True,
                )
            raise
        except asyncio.TimeoutError:
            result = {
                "ok": False, "error_type": "timeout",
                "error": f"Formatter timed out after {int(timeout_s)}s.",
                "summary": "Formatter timed out; no changes were saved.",
            }
        except UsageLimitExceeded:
            result = {
                "ok": False, "error_type": "turn_budget",
                "error": "Formatter reached its turn budget without finishing.",
                "summary": "Formatter stopped at its turn budget; no changes were saved.",
            }
        except Exception as exc:  # noqa: BLE001 — advisory pass, per-sheet isolation
            logger.exception(
                "automatic PDF notes formatter failed run=%s sheet=%s",
                run_id, sheet,
            )
            result = {
                "ok": False, "error_type": "model_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        _persist_outcome(db_path, run_id, sheet, model_name, result)
        return sheet, result

    completed = 0

    async def tracked(sheet: str) -> tuple[str, dict[str, Any]]:
        nonlocal completed
        pair = await one(sheet)
        completed += 1
        if on_progress is not None:
            try:
                on_progress(completed, len(selected), sheet)
            except Exception:  # noqa: BLE001 — display progress is advisory
                logger.warning(
                    "automatic PDF formatter progress callback failed",
                    exc_info=True,
                )
        return pair

    pairs = await asyncio.gather(*(tracked(sheet) for sheet in selected))
    outcomes = dict(pairs)
    return {
        "sheets": outcomes,
        "formatted": sum(1 for result in outcomes.values() if result.get("ok")),
        "failed": sum(
            1 for result in outcomes.values()
            if not result.get("ok") and not result.get("skipped")
        ),
        "skipped": sum(
            1 for result in outcomes.values() if result.get("skipped")
        ),
    }
