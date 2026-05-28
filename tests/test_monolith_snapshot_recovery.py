"""Pinning tests for monolith workbook snapshot + recovery (peer-review
MEDIUM #4, 2026-05-28).

Pre-fix `_snapshot_workbook` dropped a `.xlsx.snap` sidecar but nothing
ever consumed it: the cancel / partial-merge path always pointed the
download link at `monolith_filled.xlsx` itself, so if that file was
truncated mid-`wb.save()` the user got a broken xlsx.

Post-fix:
- `_snapshot_workbook` validates the live workbook is openable BEFORE
  overwriting the `.snap`, so a corrupt live file can't poison the
  last-known-good copy.
- `_resolve_workbook_for_recovery` is the recovery consumer: returns
  the live file if openable, else the `.snap` if openable, else None.
- The cancel path uses the helper, so the download link always points
  at a file Excel can actually open.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl
import pytest

from monolith.coordinator import (
    _is_xlsx_openable,
    _resolve_workbook_for_recovery,
    _snapshot_workbook,
)


REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def _good_xlsx(target: Path) -> None:
    shutil.copyfile(TEMPLATE, target)


def _corrupt_xlsx(target: Path, *, fragment: bytes = b"") -> None:
    """Write a truncated/corrupt file to ``target`` to simulate a
    crashed `wb.save()`. Empty `fragment` writes a zero-byte file
    (extreme case); a non-empty fragment writes a partial zip prefix."""
    target.write_bytes(fragment)


def test_is_xlsx_openable_true_for_good_workbook(tmp_path):
    path = tmp_path / "good.xlsx"
    _good_xlsx(path)
    assert _is_xlsx_openable(path) is True


def test_is_xlsx_openable_false_for_truncated_workbook(tmp_path):
    path = tmp_path / "bad.xlsx"
    _corrupt_xlsx(path)
    assert _is_xlsx_openable(path) is False


def test_is_xlsx_openable_false_for_missing_path(tmp_path):
    assert _is_xlsx_openable(tmp_path / "missing.xlsx") is False


def test_snapshot_creates_snap_sidecar(tmp_path):
    """A snapshot taken when the live workbook is healthy produces a
    `.xlsx.snap` next to it that itself opens cleanly."""
    live = tmp_path / "monolith_filled.xlsx"
    _good_xlsx(live)
    _snapshot_workbook(live)
    snap = live.with_suffix(".xlsx.snap")
    assert snap.exists()
    assert _is_xlsx_openable(snap)


def test_snapshot_does_not_overwrite_with_corrupt_live(tmp_path):
    """If the live workbook is unreadable when `_snapshot_workbook` is
    called (caught mid-`wb.save()` by a CancelledError), the previous
    `.snap` must survive — propagating the corruption would defeat the
    crash-recovery purpose."""
    live = tmp_path / "monolith_filled.xlsx"
    # 1. Healthy live + one good snapshot.
    _good_xlsx(live)
    _snapshot_workbook(live)
    snap = live.with_suffix(".xlsx.snap")
    assert _is_xlsx_openable(snap)
    original_size = snap.stat().st_size

    # 2. Live now goes corrupt (mid-save truncation).
    _corrupt_xlsx(live)
    assert not _is_xlsx_openable(live)

    # 3. Snapshot the corrupt live: should be a no-op for the snap.
    _snapshot_workbook(live)
    assert _is_xlsx_openable(snap), (
        "previous good snapshot was overwritten with corruption; the "
        "crash-recovery contract is broken"
    )
    assert snap.stat().st_size == original_size


def test_resolve_returns_live_when_openable(tmp_path):
    live = tmp_path / "monolith_filled.xlsx"
    _good_xlsx(live)
    _snapshot_workbook(live)
    assert _resolve_workbook_for_recovery(live) == live


def test_resolve_falls_back_to_snap_when_live_corrupt(tmp_path):
    live = tmp_path / "monolith_filled.xlsx"
    snap = live.with_suffix(".xlsx.snap")
    # Healthy snap exists, live is corrupted.
    _good_xlsx(live)
    _snapshot_workbook(live)
    _corrupt_xlsx(live)
    assert not _is_xlsx_openable(live)
    assert _is_xlsx_openable(snap)
    recovered = _resolve_workbook_for_recovery(live)
    assert recovered == snap


def test_resolve_returns_none_when_both_unreadable(tmp_path):
    live = tmp_path / "monolith_filled.xlsx"
    _corrupt_xlsx(live)
    # No snap created.
    assert _resolve_workbook_for_recovery(live) is None
