"""Automatic PDF-notes formatting after extraction and notes review.

PDF content is persisted structure-first. This module runs the existing
style-only formatter over unstyled prose cells, in parallel by sheet, and
persists the same task rows the manual Notes-tab action uses. Word runs are
excluded by the server caller; their source-formatting contract is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from collections.abc import Callable, Iterable
from typing import Any

from pydantic_ai.exceptions import UsageLimitExceeded

from db import repository as repo
from notes.formatting_agent import (
    formatter_cell_is_candidate, list_formatter_cells_for_run, run_notes_formatter,
)

logger = logging.getLogger(__name__)

PDF_FORMAT_CANDIDATE_SOURCES = {"unstyled", "floor"}


def candidate_sheets(
    db_path: str, run_id: int, requested_sheets: Iterable[str],
) -> list[str]:
    """Return requested sheets with at least one unstyled prose cell."""
    requested = set(requested_sheets)
    with repo.db_session(db_path) as conn:
        cells = list_formatter_cells_for_run(conn, run_id)
    return sorted({
        c.sheet for c in cells
        if c.sheet in requested
        and formatter_cell_is_candidate(c, PDF_FORMAT_CANDIDATE_SOURCES)
    })


# Cells per concurrent formatter request. A large List-of-Notes sheet (about a
# dozen tables) otherwise waits on one request that reads every source page and
# writes every patch; a failed repair then repeats all of it.
FORMAT_ROWS_PER_PART = 4
FORMAT_MAX_CONCURRENT_REQUESTS = 4


def _precise_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _row_groups(db_path: str, run_id: int, sheet: str) -> list[list[int]]:
    """Split a sheet's formatter candidates into ordered row groups."""
    with repo.db_session(db_path) as conn:
        rows = sorted(
            c.row for c in list_formatter_cells_for_run(conn, run_id)
            if c.sheet == sheet
            and formatter_cell_is_candidate(c, PDF_FORMAT_CANDIDATE_SOURCES)
        )
    return [
        rows[i:i + FORMAT_ROWS_PER_PART]
        for i in range(0, len(rows), FORMAT_ROWS_PER_PART)
    ]


def _failure_result(
    exc: BaseException, timeout_s: float, run_id: int, sheet: str,
) -> dict[str, Any]:
    if isinstance(exc, asyncio.TimeoutError):
        return {
            "ok": False, "error_type": "timeout",
            "error": f"Formatter timed out after {int(timeout_s)}s.",
            "summary": "Formatter timed out; no changes were saved.",
        }
    if isinstance(exc, UsageLimitExceeded):
        return {
            "ok": False, "error_type": "turn_budget",
            "error": "Formatter reached its turn budget without finishing.",
            "summary": "Formatter stopped at its turn budget; no changes were saved.",
        }
    logger.error(
        "automatic PDF notes formatter failed run=%s sheet=%s",
        run_id, sheet, exc_info=exc,
    )
    return {
        "ok": False, "error_type": "model_error",
        "error": f"{type(exc).__name__}: {exc}",
    }


_TOKEN_FIELDS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
)


def merge_part_results(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine row-group outcomes into one sheet outcome.

    A group with nothing left to format is not a failure. The sheet is ``ok``
    only when every remaining group is; saved rows from successful groups are
    reported even when another group failed, so the run records a partial
    result instead of hiding written formatting.
    """
    active = [
        p for p in parts if p.get("error_type") != "no_unfinished_rows"
    ] or parts[:1]
    failures = [p for p in active if not p.get("ok")]
    merged: dict[str, Any] = {
        "ok": not failures,
        "changed_rows": sum(int(p.get("changed_rows") or 0) for p in active),
        "skipped_rows": sorted(
            row for p in active for row in p.get("skipped_rows") or []
        ),
        "summary": " ".join(dict.fromkeys(
            str(p["summary"]) for p in active if p.get("summary")
        )),
        "parts": len(parts),
    }
    for field_name in _TOKEN_FIELDS:
        merged[field_name] = sum(int(p.get(field_name) or 0) for p in parts)
    visual_reviews = [p["visual_review"] for p in active if "visual_review" in p]
    if visual_reviews:
        merged["visual_reviews"] = visual_reviews
    if failures:
        merged["error_type"] = failures[0].get("error_type")
        merged["error"] = "; ".join(
            str(p.get("error")) for p in failures if p.get("error")
        )
        failed_rows = sorted(
            row for p in failures for row in p.get("failed_rows") or []
        )
        if failed_rows:
            merged["failed_rows"] = failed_rows
            merged["row_errors"] = {
                row: error for p in failures
                for row, error in (p.get("row_errors") or {}).items()
            }
    return merged


def _persist_outcome(
    db_path: str, run_id: int, sheet: str, model_name: str,
    result: dict[str, Any],
) -> None:
    with repo.db_session(db_path) as conn:
        repo.upsert_notes_format_task(
            conn, run_id, sheet, "done", model=model_name,
            summary=result.get("summary"),
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
    on_phase: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Format eligible PDF-note sheets and return an advisory summary.

    Sheet failures are isolated and persisted. Cancellation is propagated so
    Stop All retains its run-level meaning.
    """
    selected = candidate_sheets(db_path, run_id, sheets)
    if not selected:
        return {"sheets": {}, "formatted": 0, "partial": 0, "failed": 0, "skipped": 0}
    model_slots = asyncio.Semaphore(FORMAT_MAX_CONCURRENT_REQUESTS)
    if on_progress is not None:
        try:
            on_progress(0, len(selected), None)
        except Exception:  # noqa: BLE001 — display progress is advisory
            logger.warning(
                "automatic PDF formatter progress callback failed",
                exc_info=True,
            )

    async def _bounded(**kwargs):
        async with model_slots:
            if on_phase is not None:
                kwargs["on_phase"] = lambda message: on_phase(kwargs["sheet"], message)
            coro = formatter(model=model_factory(), **kwargs)
            if timeout_s and timeout_s != float("inf"):
                return await asyncio.wait_for(coro, timeout=timeout_s)
            return await coro

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
                groups = _row_groups(db_path, run_id, sheet)
                if len(groups) <= 1:
                    result = await _bounded(
                        run_id=run_id, db_path=db_path, pdf_path=pdf_path,
                        sheet=sheet, output_dir=output_dir,
                        style_sources=PDF_FORMAT_CANDIDATE_SOURCES,
                    )
                else:
                    # One long request per large sheet was the slowest step
                    # after review. Disjoint row groups run concurrently, share
                    # one revert snapshot, and fail independently.
                    pass_started_at = _precise_now()

                    async def part(index: int, group: list[int]) -> dict[str, Any]:
                        try:
                            return await _bounded(
                                run_id=run_id, db_path=db_path, pdf_path=pdf_path,
                                sheet=sheet,
                                output_dir=output_dir,
                                style_sources=PDF_FORMAT_CANDIDATE_SOURCES,
                                rows=group, pass_started_at=pass_started_at,
                                trace_label=f"notes_format_{sheet}_part{index}",
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001 — per-part isolation
                            return _failure_result(exc, timeout_s, run_id, sheet)

                    result = merge_part_results(await asyncio.gather(*(
                        part(index, group)
                        for index, group in enumerate(groups, start=1)
                    )))
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
        except Exception as exc:  # noqa: BLE001 — advisory pass, per-sheet isolation
            result = _failure_result(exc, timeout_s, run_id, sheet)
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
        "partial": sum(
            1 for result in outcomes.values()
            if not result.get("ok") and not result.get("skipped")
            and (result.get("changed_rows") or 0) > 0
        ),
        "failed": sum(
            1 for result in outcomes.values()
            if not result.get("ok") and not result.get("skipped")
            and not (result.get("changed_rows") or 0)
        ),
        "skipped": sum(
            1 for result in outcomes.values() if result.get("skipped")
        ),
    }
