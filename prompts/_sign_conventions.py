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
    a `*Total …` formula, alongside its add/subtract sign. Serves SOCF
    and the SoRE (SOCIE-family) statement — the title and wording are
    statement-neutral so SoRE no longer receives SOCF-branded prose.

    Returns None if the template can't be read or carries no `*Total`
    formulas — the agent falls back to the static generic rules. The
    matrix SOCIE sheet (named "SOCIE") is filtered out below, so only
    SoRE among the SOCIE family produces a block.

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

    # Always close the workbook (file handle + in-memory archive) on every
    # exit path — this helper runs at prompt-build time and the codebase
    # convention is to close openpyxl workbooks (Windows handle hazard,
    # gotcha #22). The string-building below touches no worksheet, so the
    # close happens once the rows are read.
    try:
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
            lowered = label.lower()
            # "net " covers the MFRS SOCF-Direct totals ("Net cash flows
            # from (used in) …", "Net increase (decrease) …"), whose labels
            # carry neither a "*" prefix nor the word "total" — pre-fix,
            # the block silently skipped that whole sheet. Leaf rows like
            # "Net repayment from joint ventures" also pass this label
            # gate but are filtered right below: they have no formula.
            if not (
                "total" in lowered
                or label.startswith("*")
                or lowered.startswith("net ")
            ):
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
    finally:
        wb.close()

    if not entries:
        return None

    lines = [
        "=== PER-ROW SIGN CONVENTIONS — AUTHORITATIVE (from live template formulas) ===",
        "",
        "These signs are read directly from THIS template's live `*Total …`",
        "formulas, so for the rows listed below they OVERRIDE any general",
        "sign rule stated earlier in this prompt. (Rows not listed here fall",
        "back to the general sign rules above — this block is the single",
        "source of truth wherever the two disagree.)",
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
        "THE ONE RULE THAT ALWAYS WORKS (apply it to every row, especially "
        "when a row's name is ambiguous): first decide the line's actual "
        "cash-flow contribution C — POSITIVE when it INCREASES cash (an "
        "inflow, a non-cash add-back to profit, a gain being reversed out), "
        "NEGATIVE when it DECREASES cash (an outflow, a deduction from profit, "
        "a loss). Then enter V = C / coefficient. So for an ADDED row "
        "(coefficient +1) enter V = C as-is; for a SUBTRACTED row (coefficient "
        "-1) enter V = -C — flip the sign, because the formula flips it back. "
        "This rule needs no judgement about the row's name and works for every "
        "row, statement and standard. The name-based hints below are just this "
        "rule spelled out for the common cases."
    )
    lines.append("")
    lines.append(
        "Worked examples of V = C / coefficient on SUBTRACTED rows (the case "
        "most often entered backwards):"
    )
    lines.append(
        "  - A SUBTRACTED gain on disposal of PPE has cash contribution "
        "C = -31,276 (a gain is deducted from profit) → enter V = -C = "
        "+31,276. Do NOT pre-negate the gain: the formula already subtracts "
        "the row, so entering -31,276 would double-negate and wrongly ADD the "
        "gain back to operating cash."
    )
    lines.append(
        "  - A SUBTRACTED non-cash add-back — e.g. 'Adjustments for accrued "
        "expenses (income) not yet paid (received)' — has C = +62,264 (a "
        "non-cash accrual added back to profit) → enter V = -C = -62,264. The "
        "blanket 'enter a positive magnitude' instinct is WRONG here: on a "
        "SUBTRACTED row a positive entry produces a NEGATIVE contribution."
    )
    lines.append("")
    lines.append(
        "If the formula ADDS a row, the total uses the cell's value AS-IS "
        "(no sign flip), so YOU must supply the correct sign:"
    )
    lines.append(
        "  - An ADDED row that is a cash OUTFLOW — its name is a 'payment', "
        "'repayment', 'purchase', 'repurchase', 'acquisition', 'deposit "
        "placed', a '…paid' line (dividends/interest/tax paid), or issuance "
        "'expenses' — takes a NEGATIVE value. Do NOT enter the bare positive "
        "magnitude: because the total ADDS (not subtracts) the cell, a "
        "positive number would wrongly INCREASE the section subtotal. "
        "(Worked example: 'Cash payments for the principal portion of the "
        "lease liability' is ADDED, so enter -3,732, NOT 3,732.)"
    )
    lines.append(
        "  - An ADDED row that is a cash INFLOW — 'Proceeds', 'Receipts', "
        "'Withdrawal', 'Dividends received', 'Interest received' — takes a "
        "POSITIVE value."
    )
    lines.append(
        "  - An ADDED gain/loss adjustment row follows its directional name: "
        "a 'Loss on disposal' takes a POSITIVE loss magnitude; a gain takes "
        "NEGATIVE."
    )
    lines.append(
        "If the formula SUBTRACTS a row, the total flips the cell's sign, so "
        "enter V = -C (the negative of the line's cash contribution):"
    )
    lines.append(
        "  - A SUBTRACTED cash OUTFLOW — 'Dividends paid', 'Cash payments', "
        "tax/interest paid — has C negative, so V = -C is a POSITIVE magnitude "
        "(do NOT pre-negate it)."
    )
    lines.append(
        "  - A SUBTRACTED gain/loss adjustment is the MIRROR of the ADDED "
        "gain/loss rule: a GAIN (C negative, deducted from profit) → enter a "
        "POSITIVE magnitude; a LOSS (C positive, added back) → enter NEGATIVE."
    )
    lines.append(
        "  - A SUBTRACTED non-cash add-back (accruals/provisions not yet paid, "
        "C positive) → enter NEGATIVE."
    )
    lines.append(
        "NOTE the contrast between the two branches: the SAME 'Cash payments' "
        "or 'gain on disposal' wording flips entry sign depending on whether "
        "THIS template's formula adds or subtracts that specific row — always "
        "obey the per-row ADDED/SUBTRACTED label listed above, not the row's "
        "name alone."
    )
    return "\n".join(lines)
