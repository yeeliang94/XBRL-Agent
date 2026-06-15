"""Shadow-diff harness for the Excel-free verification migration (item 32).

The migration discipline (docs/PLAN-excel-free-verification.md) is: run the
old (xlsx) path and the new (fact-based) path on the same fixtures and prove
they agree BEFORE the xlsx path is retired. This module holds the equality
assertions both phases reuse.

Equality is checked at **display precision** (2 decimals) for money fields,
not raw float equality — the cascade rounds to cents (``_money``) while
openpyxl evaluates formulas exactly, and both surfaces render via ``:.2f``,
so a sub-cent representational difference is noise, not a real disagreement
(decision Q2 in the plan). Everything else (status strings, messages, cell
coordinates) must match exactly.

This is a test *helper*, not a test module — pytest does not collect it (the
filename has no ``test_`` prefix). Its own self-test lives in
``tests/test_shadow_diff.py``.
"""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Optional

# A decimal token embedded in a message string, e.g. "1000.0" or "-95.5". The
# cross-check messages interpolate raw floats (``assets ({assets_cy})``); the
# cascade and openpyxl can render the same money value with different trailing
# digits, so we round these to display precision before comparing the message.
_DECIMAL_RE = re.compile(r"-?\d+\.\d+")


def nums_equal(a: Optional[float], b: Optional[float], *, dp: int = 2) -> bool:
    """Money equality at ``dp`` decimals. ``None`` matches only ``None``."""
    if a is None or b is None:
        return a is None and b is None
    return round(float(a), dp) == round(float(b), dp)


def _normalize_message(message: Optional[str], *, dp: int = 2) -> Optional[str]:
    """Round every embedded decimal token in a message to ``dp`` places so two
    messages that differ only in float-repr noise compare equal. Integers and
    words are left untouched (only ``\\d+\\.\\d+`` tokens are rewritten)."""
    if not message:
        return message

    def _round(m: re.Match) -> str:
        return str(round(float(m.group(0)), dp))

    return _DECIMAL_RE.sub(_round, message)


def _comparands_equal(xs, ys, *, dp: int = 2) -> bool:
    """Compare two Comparand lists: exact on label/sheet/role/statement/row,
    money-precision on value. Order-sensitive (both paths build them in the
    same order)."""
    xs = xs or []
    ys = ys or []
    if len(xs) != len(ys):
        return False
    for x, y in zip(xs, ys):
        xd, yd = asdict(x), asdict(y)
        if not nums_equal(xd.pop("value", None), yd.pop("value", None), dp=dp):
            return False
        if xd != yd:
            return False
    return True


def cross_check_diff(xlsx_result, fact_result, *, dp: int = 2) -> list[str]:
    """Return a list of human-readable field differences (empty == parity).

    Compares the two ``CrossCheckResult`` objects field by field: status,
    message, target coords, and comparands exactly; the numeric
    expected/actual/diff/tolerance at ``dp`` decimals.
    """
    diffs: list[str] = []

    for field in ("name", "status", "target_sheet", "target_row"):
        xv = getattr(xlsx_result, field, None)
        fv = getattr(fact_result, field, None)
        if xv != fv:
            diffs.append(f"{field}: xlsx={xv!r} != facts={fv!r}")

    # Message: identical word-for-word after rounding embedded decimals.
    xmsg = _normalize_message(getattr(xlsx_result, "message", None), dp=dp)
    fmsg = _normalize_message(getattr(fact_result, "message", None), dp=dp)
    if xmsg != fmsg:
        diffs.append(
            f"message: xlsx={getattr(xlsx_result, 'message', None)!r} != "
            f"facts={getattr(fact_result, 'message', None)!r}"
        )

    for field in ("expected", "actual", "diff", "tolerance"):
        xv = getattr(xlsx_result, field, None)
        fv = getattr(fact_result, field, None)
        if not nums_equal(xv, fv, dp=dp):
            diffs.append(f"{field}: xlsx={xv!r} != facts={fv!r}")

    if not _comparands_equal(
        getattr(xlsx_result, "comparands", None),
        getattr(fact_result, "comparands", None),
        dp=dp,
    ):
        diffs.append(
            "comparands: "
            f"xlsx={getattr(xlsx_result, 'comparands', None)!r} != "
            f"facts={getattr(fact_result, 'comparands', None)!r}"
        )

    return diffs


def assert_cross_check_parity(xlsx_result, fact_result, *, dp: int = 2) -> None:
    """Raise ``AssertionError`` with a readable diff unless the two
    ``CrossCheckResult`` objects are shadow-equal."""
    diffs = cross_check_diff(xlsx_result, fact_result, dp=dp)
    if diffs:
        raise AssertionError(
            "cross-check shadow-diff mismatch:\n  " + "\n  ".join(diffs)
        )
