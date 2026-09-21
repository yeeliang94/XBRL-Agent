"""Shared atomic workbook persistence (PLAN-orchestration-hardening item 8).

openpyxl's ``wb.save`` rewrites the ``.xlsx`` zip in place, which is not
atomic on any platform: a concurrent reader can observe a truncated zip and
raise ``EOFError`` (gotcha #22, 2026-05-29 Windows incident). Every live
code path that saves a workbook another tool may read must go through
``atomic_save_workbook`` instead of ``wb.save(path)``.

The helper originated as a private tempfile+atomic-replace shape in the
notes post-validator; it was promoted here (item 8) so every saver —
``tools/fill_workbook.py``, ``concept_model/exporter.py``,
``notes/writer.py``, ``workbook_merger.py`` — shares one mechanism. The final
promotion uses the cross-platform bounded retry policy in ``utils.atomic_io``.
"""
from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

from utils.atomic_io import replace_with_retry

if TYPE_CHECKING:  # pragma: no cover — import only for type checkers
    import openpyxl


def atomic_save_workbook(wb: "openpyxl.Workbook", path: str) -> None:
    """Save ``wb`` to ``path`` atomically.

    Writes to a sibling tempfile on the same filesystem and atomically
    replaces the destination, retrying bounded transient file locks.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=directory)
    os.close(fd)
    try:
        wb.save(tmp_path)
        replace_with_retry(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
