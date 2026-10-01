"""Arithmetic checks inside the numeric notes (share capital, related parties).

Neither the SSM taxonomy nor the mTool template carries a formula on these
sheets: every figure, including a Total column and a closing balance, is typed.
A slip therefore survives every other check. This reads the run's facts and
reports, as advisory warnings, any place where the stated figures do not add
up. It never writes or derives a value.

* Category total: a field's total (no category, or the axis' total member)
  equals the sum of its categories (share classes, related-party categories).
* Roll-forward: in each share-capital movement block, closing balance equals
  opening balance plus the movements, per category and period.
* Opening balance: the current year's opening equals the prior year's closing.

A rule is checked only where every figure it needs was extracted; a missing
figure is not treated as zero, except a movement line, which is blank when
nothing moved.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from concept_model.dimensions import numeric_category_catalog
from concept_model.parser import _derive_template_id
from notes_types import NotesTemplateType, notes_template_path

TOLERANCE = 1.0


@dataclass(frozen=True)
class _Block:
    title: str
    balance: str               # the opening and closing balance concept
    issued: tuple[str, ...]    # the "issued during the year" line
    components: tuple[str, ...] = ()  # breakdown used when ``issued`` is blank
    other: str = ""


# Local taxonomy names (the part after the ``prefix_``), shared by MFRS and
# MPERS unless listed as alternatives. The opening and closing rows share the
# balance concept; the opening is the earlier row.
_BLOCKS = (
    _Block(
        "Amount of shares issued and fully paid",
        balance="AmountOfSharesIssuedAndFullyPaidOutstanding",
        issued=("AmountOfSharesIssuedAndFullyPaidDuringPeriod",),
        components=(
            "AmountOfSharesIssuedForCashUnderESOS",
            "AmountOfSharesIssuedForCashUnderPrivatePlacement",
            "AmountOfSharesArisingFromConversionOfICULSBySurrenderOption",
            "AmountOfSharesArisingFromConversionOfICULSByMandatoryConversion",
        ),
        other="AmountOfOtherChangesInSharesIssuedAndFullyPaid",
    ),
    _Block(
        "Number of shares outstanding",
        balance="NumberOfSharesOutstanding",
        issued=("NumberOfOutstandingSharesIssuedDuringPeriod", "NumberOfSharesIssued"),
        other="OtherChangesInNumberOfSharesOutstandingDuringPeriod",
    ),
    _Block(
        "Amount of shares outstanding",
        balance="AmountOfSharesOutstanding",
        issued=("AmountOfOutstandingSharesIssuedDuringPeriod",),
        other="OtherChangesInAmountOfSharesOutstanding",
    ),
)

_TOPICS = {
    NotesTemplateType.ISSUED_CAPITAL: "Issued capital",
    NotesTemplateType.RELATED_PARTY: "Related parties",
}


@dataclass
class FootingFinding:
    topic: str
    field: str
    category: str
    period: str
    scope: str
    expected: float
    actual: float
    message: str


def _local(name: str | None) -> str:
    return (name or "").split("_", 1)[-1]


def _member_label(member: str) -> str:
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", _local(member).removesuffix("Member"))
    return words[:1].upper() + words[1:].lower()


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def check_notes_numeric_footing(
    conn: sqlite3.Connection,
    run_id: int,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
) -> list[FootingFinding]:
    """Every place the run's share-capital and related-party figures do not
    add up. Read-only."""
    totals = {member for roots in numeric_category_catalog(
        filing_standard, roots_only=True).values() for member in roots}
    findings: list[FootingFinding] = []
    for template_type, topic in _TOPICS.items():
        try:
            template_id = _derive_template_id(Path(notes_template_path(
                template_type, filing_level, filing_standard)))
        except ValueError:
            continue
        # (concept, period, scope, category) -> value; category "" is the total.
        values: dict[tuple[str, str, str, str], float] = {}
        concepts: dict[str, tuple[str, str, int]] = {}
        for uuid, label, primary, row, period, scope, dim_key, value in conn.execute(
            "SELECT f.concept_uuid, n.canonical_label, sa.primary_concept, "
            "n.render_row, f.period, f.entity_scope, f.dimension_key, f.value "
            "FROM run_concept_facts f "
            "JOIN concept_nodes n ON n.concept_uuid = f.concept_uuid "
            "LEFT JOIN concept_semantic_addresses sa ON sa.concept_uuid = f.concept_uuid "
            "WHERE f.run_id = ? AND n.template_id = ? AND f.value IS NOT NULL "
            "AND COALESCE(f.value_status, '') != 'not_disclosed'",
            (run_id, template_id),
        ):
            members = list(json.loads(dim_key or "{}").values())
            category = "" if not members or members[0] in totals else members[0]
            values.setdefault((uuid, period, scope, category), float(value))
            concepts[uuid] = (label.lstrip("*").strip(), _local(primary), row or 0)

        by_name: dict[str, list[str]] = {}
        for uuid, (_label, name, _row) in sorted(concepts.items(), key=lambda i: i[1][2]):
            by_name.setdefault(name, []).append(uuid)
        # "Balance at the end of period" alone does not say which block.
        block_of = {uuid: block.title for block in _BLOCKS
                    for uuid in by_name.get(block.balance, [])}

        def report(uuid, period, scope, category, expected, actual, detail):
            if abs(expected - actual) <= TOLERANCE:
                return
            field = concepts[uuid][0]
            if uuid in block_of and field.lower().startswith("balance"):
                field = f"{block_of[uuid]}, {field[:1].lower()}{field[1:]}"

            where = ", ".join(filter(None, [
                _member_label(category) if category else "Total", period,
                scope if filing_level == "group" else ""]))
            findings.append(FootingFinding(
                topic=topic, field=field,
                category=_member_label(category) if category else "Total",
                period=period, scope=scope, expected=expected, actual=actual,
                message=f"{topic}, {field} ({where}): {detail}"))

        # Category total = sum of categories.
        groups: dict[tuple[str, str, str], dict[str, float]] = {}
        for (uuid, period, scope, category), value in values.items():
            groups.setdefault((uuid, period, scope), {})[category] = value
        for (uuid, period, scope), by_category in sorted(groups.items()):
            parts = {c: v for c, v in by_category.items() if c}
            if "" in by_category and parts:
                total = sum(parts.values())
                report(uuid, period, scope, "", total, by_category[""],
                       f"the categories add up to {_fmt(total)}, but the total "
                       f"is {_fmt(by_category[''])}.")

        if template_type is not NotesTemplateType.ISSUED_CAPITAL:
            continue
        scopes = {(p, s, c) for (_u, p, s, c) in values}
        for block in _BLOCKS:
            balances = by_name.get(block.balance, [])
            if len(balances) != 2:
                continue  # the block needs its own opening and closing rows
            opening, closing = balances
            for period, scope, category in sorted(scopes):
                def get(uuid, p=period):
                    return values.get((uuid, p, scope, category))

                start, end = get(opening), get(closing)
                if start is not None and end is not None:
                    issued = next((get(u) for name in block.issued
                                   for u in by_name.get(name, []) if get(u) is not None),
                                  None)
                    if issued is None:
                        issued = sum(get(u) or 0.0 for name in block.components
                                     for u in by_name.get(name, []))
                    other = sum(get(u) or 0.0 for u in by_name.get(block.other, []))
                    expected = start + issued + other
                    report(closing, period, scope, category, expected, end,
                           f"opening {_fmt(start)} plus movements "
                           f"{_fmt(issued + other)} is {_fmt(expected)}, but the "
                           f"closing balance is {_fmt(end)}.")
                prior_end = get(closing, "PY")
                if period == "CY" and start is not None and prior_end is not None:
                    report(opening, period, scope, category, prior_end, start,
                           f"the opening balance {_fmt(start)} differs from the "
                           f"prior year's closing balance {_fmt(prior_end)}.")
    return findings
