"""Build per-row sign-convention guidance from a live template's formulas.

RUN-REVIEW P2-2 (2026-04-26): the Amway run had `(Gain) loss on disposal
of PPE` AI=-70 vs filer=70, and `Cash payments for the principal portion
of lease liabilities` AI=3,732 vs filer=-3,732. Both are valid signs in
isolation; which one is "right" depends on whether the *Total formula
adds or subtracts that cell. Mirroring the ADR-002 pattern for SOCIE
dividends, this module walks the live template's formula bar at
prompt-build time and surfaces a per-row signed-convention block to
the agent.

Use from the prompt-build path::

    from prompts._sign_conventions import socf_sign_convention_block
    extra = socf_sign_convention_block(template_path)
    if extra:
        prompt += "\n\n" + extra
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook

# Match a single signed-coefficient term in a *Total formula:
#   "+1*B11"        →  sign +1, ref B11
#   "-1*B13"        →  sign -1, ref B13
#   "1*B11"         →  sign +1, ref B11 (leading + omitted)
# The pre-pended sign captures the term-separator from a SUM expression.
_TERM_RE = re.compile(r"([+-]?\s*1)\s*\*\s*([A-Z]+)(\d+)")


def _parse_total_formula(formula: str) -> list[tuple[int, str, int]]:
    """Return [(sign, col_letter, row), ...] for each ±1*<cell> term.

    Returns empty list for formulas we don't recognise (SUM(), unusual
    forms, multi-row ranges) — the agent falls back to the generic
    sign rules in the prompt for those.
    """
    if not formula or not formula.startswith("="):
        return []
    out: list[tuple[int, str, int]] = []
    for sign_part, col, row in _TERM_RE.findall(formula):
        sign = -1 if "-" in sign_part else 1
        out.append((sign, col, int(row)))
    return out


def _label_at(ws, row: int) -> str:
    val = ws.cell(row, 1).value
    return str(val).strip() if val else ""


def socf_sign_convention_block(template_path: str | Path) -> Optional[str]:
    """Build a prompt-injectable block listing each row that flows into
    a SOCF `*Total …` formula, alongside its add/subtract sign.

    Returns None if the template can't be read or carries no `*Total`
    formulas — the agent falls back to the static generic rules.

    The block is intentionally terse so the prompt cache stays warm.
    Each row appears AT MOST ONCE in the output even when it feeds
    multiple totals (the first occurrence wins).
    """
    p = Path(template_path)
    if not p.exists():
        return None

    try:
        wb = load_workbook(p, data_only=False)
    except Exception:  # noqa: BLE001
        return None

    # SOCF templates have one sheet but the helper is defensive.
    target_sheet = None
    for sn in wb.sheetnames:
        if "socf" in sn.lower() or "sore" in sn.lower():
            target_sheet = sn
            break
    if target_sheet is None:
        return None
    ws = wb[target_sheet]

    seen_rows: set[int] = set()
    entries: list[tuple[int, int, str]] = []  # (target_row, sign, leaf_label)

    # Walk every row; if its label is a Total/subtotal AND col B has
    # a formula we recognise, emit one line per leaf in that formula.
    for r in range(1, ws.max_row + 1):
        label = _label_at(ws, r)
        if not label:
            continue
        if not ("total" in label.lower() or label.startswith("*")):
            continue
        formula = ws.cell(r, 2).value
        if not isinstance(formula, str) or not formula.startswith("="):
            continue
        terms = _parse_total_formula(formula)
        if not terms:
            continue
        for sign, _col, leaf_row in terms:
            if leaf_row in seen_rows:
                continue
            seen_rows.add(leaf_row)
            leaf_label = _label_at(ws, leaf_row)
            if not leaf_label:
                continue
            entries.append((leaf_row, sign, leaf_label))

    if not entries:
        return None

    lines = [
        "=== SOCF SIGN CONVENTIONS (from live template formulas) ===",
        "",
        "Each row below appears in a `*Total …` formula with the indicated",
        "coefficient. Enter values to MATCH the formula's intent:",
        "",
    ]
    for leaf_row, sign, leaf_label in sorted(entries):
        sign_word = "ADDED" if sign > 0 else "SUBTRACTED"
        # Truncate very long labels; the agent doesn't need the full
        # SSM URI suffix, just enough to recognise the row.
        if len(leaf_label) > 80:
            leaf_label = leaf_label[:77] + "..."
        lines.append(
            f"- Row {leaf_row} `{leaf_label}` is {sign_word} by its total."
        )
    lines.append("")
    lines.append(
        "If the formula ADDS a row, enter the magnitude that matches the "
        "row's directional name (a 'Loss on disposal' row added by the "
        "total takes a POSITIVE loss magnitude; a gain takes NEGATIVE)."
    )
    lines.append(
        "If the formula SUBTRACTS a row, enter the magnitude that flips "
        "the directional name (a 'Cash payments' row subtracted by the "
        "total takes a POSITIVE outflow magnitude — the formula handles "
        "the sign flip)."
    )
    return "\n".join(lines)
