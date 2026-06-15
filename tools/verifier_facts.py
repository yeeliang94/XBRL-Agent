"""Fact-based verification (item 32, Phase 2 / 32b).

Reads the cascade-persisted totals from ``run_concept_facts`` by uuid instead of
opening the workbook and evaluating formulas. Gated behind
``XBRL_FACT_BASED_VERIFY`` (default off — see ``_fact_based_verify_enabled`` in
``tools/verifier.py``). Covers all five face statements (SOFP, SOCIE, SOCF, SOPL,
SOCI); a statement whose fact path is not implemented signals the caller (return
``None``) to fall back to the xlsx path.

Why this exists: ``verify_statement`` is one of the last three xlsx consumers in
the verification path. The cascade already persists every COMPUTED total into
``run_concept_facts`` (``concept_model/cascade.py``, ``source='cascade'``), so the
math truth the formula cells encode is already in the DB by concept uuid. This
reads those rows instead of re-evaluating formulas.

Mapping from xlsx geometry to fact space:

* The xlsx verifiers iterate ``_cy_columns(filing_level)`` — col B (Company/Group
  CY) and, on group filings, col D (Company CY). In fact space those are the
  ``(CY, entity_scope)`` keys: col B → primary scope (Group on a group filing,
  else Company); col D → Company. ``_cy_scopes`` encodes this.
* SOFP additionally reads PY (col C / col E) — ``(PY, scope)``.
* The SOCIE matrix's vertical blocks (group_cy / group_py / company_cy /
  company_py) map to the four ``(period, entity_scope)`` combinations; the Total
  column (MFRS ``X`` / MPERS ``B``) is read by ``matrix_col``.

Parity contract vs the xlsx path (proven by ``tests/test_verifier_shadow.py``):

* ``computed_totals`` / ``is_balanced`` / ``mismatches`` / ``feedback`` /
  ``matches_pdf`` / ``magnitude_warnings`` — byte-compatible. The imbalance
  feedback wording (gotcha #17) is produced by the SAME ``_compose_feedback`` /
  ``_imbalance_diagnostic`` / ``_sofp_imbalance_feedback`` helpers the xlsx path
  uses.
* ``mandatory_unfilled`` — **INTENTIONALLY stricter** (product decision
  2026-06-14). The xlsx ``_collect_unfilled_mandatory`` treats any formula cell
  as "filled", and the face sheets pre-fill cross-sheet formulas, so that scan is
  near-inert. The fact path flags a mandatory (``*``) leaf whose fact is
  genuinely ABSENT. A ``not_disclosed`` fact counts as resolved (the agent
  confirmed there is no value), NOT unfilled — a distinction the xlsx path cannot
  make. This feeds the save gate (gotcha #17), so it stays behind the
  off-by-default flag until validated.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Optional

from concept_model.facts_api import read_run_facts
from tools.verifier import (
    VerificationResult,
    _MAGNITUDE_LOG10_THRESHOLD,
    _MAGNITUDE_MAX_WARNINGS,
    _MAGNITUDE_MIN_VALUE,
    _SOFP_SHEET_NAME,
    _balance_tolerance,
    _check_pdf_values,
    _compose_feedback,
    _cy_columns,
    _imbalance_diagnostic,
    _normalize_label,
    _sofp_imbalance_feedback,
)


def verify_statement_facts(
    conn: sqlite3.Connection,
    run_id: int,
    template_id: str,
    statement_type: "object",
    variant: str = "",
    pdf_values: Optional[dict[str, float]] = None,
    filing_level: str = "company",
    filing_standard: str = "mfrs",
) -> Optional[VerificationResult]:
    """Fact-based twin of ``tools.verifier.verify_statement``.

    Returns a ``VerificationResult`` for statements whose fact path is
    implemented, or ``None`` to signal the caller to fall back to the xlsx path.
    The dispatch + post-dispatch magnitude scan mirror ``verify_statement`` so
    the two stay aligned.
    """
    from statement_types import StatementType

    name = statement_type.value if hasattr(statement_type, "value") else str(statement_type)

    nodes = _load_nodes(conn, template_id)
    facts = read_run_facts(conn, run_id, [template_id])

    if name == StatementType.SOFP.value:
        result = _verify_sofp_facts(nodes, facts, filing_level, pdf_values)
    elif name == StatementType.SOCIE.value:
        result = _verify_socie_facts(
            nodes, facts, variant, filing_level, filing_standard, pdf_values)
    elif name == StatementType.SOCF.value:
        result = _verify_socf_facts(nodes, facts, filing_level, pdf_values)
    elif name == StatementType.SOPL.value:
        result = _verify_sopl_facts(nodes, facts, filing_level, pdf_values)
    elif name == StatementType.SOCI.value:
        result = _verify_soci_facts(nodes, facts, filing_level, pdf_values)
    else:
        return None

    # Item 24: advisory magnitude scan — skip SOCIE (its columns are equity
    # components, not a CY/PY pair). Mirrors verify_statement exactly.
    if name != StatementType.SOCIE.value:
        result.magnitude_warnings = _scan_magnitude_warnings_facts(
            nodes, facts, filing_level)
    return result


# ---------------------------------------------------------------------------
# Node/fact readers
# ---------------------------------------------------------------------------


def _load_nodes(conn: sqlite3.Connection, template_id: str) -> list[dict]:
    """All concept nodes for a template, ordered top-to-bottom by render row —
    the fact-space analogue of scanning the workbook's column A."""
    rows = conn.execute(
        "SELECT concept_uuid, kind, canonical_label, render_sheet, render_row, "
        "       render_col, matrix_col "
        "FROM concept_nodes WHERE template_id = ? ORDER BY render_sheet, render_row",
        (template_id,),
    ).fetchall()
    return [
        {
            "uuid": r[0], "kind": r[1], "label": r[2],
            "sheet": r[3], "row": r[4], "col": r[5], "matrix_col": r[6],
        }
        for r in rows
    ]


def _fact_value(facts: dict, uuid: str, period: str, scope: str) -> Optional[float]:
    """One fact's numeric value, treating not_disclosed/blank as None — mirrors
    ``cross_checks.facts_util._fact_value`` so the two read paths agree."""
    fact = facts.get((uuid, period, scope))
    if fact is None or fact.get("value_status") == "not_disclosed":
        return None
    raw = fact.get("value")
    return float(raw) if raw is not None else None


def _primary_scope(filing_level: str) -> str:
    return "Group" if filing_level == "group" else "Company"


def _cy_scopes(filing_level: str) -> list[tuple[str, str]]:
    """(entity_scope, label_prefix) pairs for the CY data columns — the
    fact-space twin of ``_cy_columns``. Col B → primary scope; group col D →
    Company. The label prefix ("Group"/"Company"/"") is reused verbatim in the
    mismatch strings so wording stays byte-identical."""
    if filing_level == "group":
        return [("Group", "Group"), ("Company", "Company")]
    return [("Company", "")]


def _sheet_present(nodes: list[dict], candidates: list[str]) -> Optional[str]:
    """First candidate sheet that exists among the template's nodes (mirrors the
    xlsx ``for sn in sheet_names_to_try`` lookup)."""
    sheets = {n["sheet"] for n in nodes}
    for c in candidates:
        if c in sheets:
            return c
    return None


def _rows_on_sheet(nodes: list[dict], sheet: str) -> list[dict]:
    return [n for n in nodes if n["sheet"] == sheet]  # already row-ordered


def _find_total_uuid(nodes: list[dict], label: str, sheet: str) -> Optional[str]:
    """Concept on ``sheet`` whose normalised label EXACTLY equals ``label`` (the
    xlsx scan uses ``==``, not substring). Returns the last match, mirroring the
    xlsx loop's overwrite semantics."""
    found = None
    for n in nodes:
        if n["sheet"] != sheet:
            continue
        if _normalize_label(str(n["label"])) == label:
            found = n["uuid"]
    return found


def _sofp_main_sheet(nodes: list[dict]) -> str:
    sheets = {n["sheet"] for n in nodes}
    if _SOFP_SHEET_NAME in sheets:
        return _SOFP_SHEET_NAME
    for n in nodes:
        if _normalize_label(str(n["label"])) == "total assets":
            return n["sheet"]
    return sorted(sheets)[0] if sheets else _SOFP_SHEET_NAME


def _matrix_value(
    nodes: list[dict], facts: dict, sheet: str, row_label: str,
    matrix_col: str, period: str, scope: str,
) -> Optional[float]:
    """SOCIE matrix cell value at (row_label exact-match, matrix_col) for a
    given (period, scope). Tries each matching row top-to-bottom until one
    carries a fact — mirrors the cross-checks' matrix reader."""
    for n in nodes:
        if n["sheet"] != sheet or n["matrix_col"] != matrix_col:
            continue
        if _normalize_label(str(n["label"])) != row_label:
            continue
        v = _fact_value(facts, n["uuid"], period, scope)
        if v is not None:
            return v
    return None


def _matrix_label_exists(
    nodes: list[dict], sheet: str, row_label: str, matrix_col: str,
) -> bool:
    return any(
        n["sheet"] == sheet and n["matrix_col"] == matrix_col
        and _normalize_label(str(n["label"])) == row_label
        for n in nodes
    )


# ---------------------------------------------------------------------------
# SOFP
# ---------------------------------------------------------------------------


def _verify_sofp_facts(
    nodes: list[dict], facts: dict, filing_level: str,
    pdf_values: Optional[dict[str, float]],
) -> VerificationResult:
    main = _sofp_main_sheet(nodes)
    primary = _primary_scope(filing_level)

    computed_totals: dict[str, float] = {}
    is_balanced = True
    mismatches: list[str] = []
    feedback_lines: list[str] = []

    ta = _find_total_uuid(nodes, "total assets", main)
    el = _find_total_uuid(nodes, "total equity and liabilities", main)

    def _set(key: str, uuid: Optional[str], period: str, scope: str) -> None:
        if uuid is None:
            return
        v = _fact_value(facts, uuid, period, scope)
        if v is not None:
            computed_totals[key] = v

    _set("total_assets_cy", ta, "CY", primary)
    _set("total_assets_py", ta, "PY", primary)
    _set("total_equity_liabilities_cy", el, "CY", primary)
    _set("total_equity_liabilities_py", el, "PY", primary)
    if filing_level == "group":
        _set("company_total_assets_cy", ta, "CY", "Company")
        _set("company_total_assets_py", ta, "PY", "Company")
        _set("company_total_equity_liabilities_cy", el, "CY", "Company")
        _set("company_total_equity_liabilities_py", el, "PY", "Company")

    def _balance(a_key: str, e_key: str, period_label: str) -> None:
        nonlocal is_balanced
        if a_key in computed_totals and e_key in computed_totals:
            diff = computed_totals[a_key] - computed_totals[e_key]
            if abs(diff) > _balance_tolerance(
                computed_totals[a_key], computed_totals[e_key]
            ):
                is_balanced = False
                mismatches.append(
                    f"{period_label}: assets={computed_totals[a_key]} "
                    f"!= equity+liabilities={computed_totals[e_key]}"
                )
                feedback_lines.extend(_sofp_imbalance_feedback(period_label, diff))

    _balance("total_assets_cy", "total_equity_liabilities_cy", "CY")
    _balance("total_assets_py", "total_equity_liabilities_py", "PY")
    if filing_level == "group":
        _balance("company_total_assets_cy", "company_total_equity_liabilities_cy", "Company CY")
        _balance("company_total_assets_py", "company_total_equity_liabilities_py", "Company PY")

    if not computed_totals:
        is_balanced = False
        mismatches.append("No totals found in workbook — cannot verify balance")
        feedback_lines.append(
            "Action: No total rows detected. Check that the template has "
            "'Total assets' and 'Total equity and liabilities' labels."
        )

    matches_pdf = True
    if pdf_values:
        for key, expected in pdf_values.items():
            actual = computed_totals.get(key)
            if actual is None:
                mismatches.append(f"Computed total '{key}' not found")
                matches_pdf = False
            elif abs(actual - expected) > _balance_tolerance(actual, expected):
                mismatches.append(f"{key}: computed={actual}, expected={expected}")
                matches_pdf = False

    # Group equity attribution: owners + NCI == Total equity.
    if filing_level == "group":
        te_uuid = owners_uuid = nci_uuid = None
        for n in nodes:
            if n["sheet"] != main:
                continue
            norm = _normalize_label(str(n["label"]))
            if norm == "total equity":
                te_uuid = n["uuid"]
            elif "equity" in norm and "owners of parent" in norm and "attribut" in norm:
                owners_uuid = n["uuid"]
            elif norm == "non-controlling interests":
                nci_uuid = n["uuid"]
        if te_uuid and owners_uuid and nci_uuid:
            for _cy_col, prefix in _cy_columns(filing_level):
                pfx = f"{prefix} " if prefix else ""
                sfx = f"_{prefix.lower()}" if prefix else ""
                scope = "Group" if prefix == "Group" else "Company"
                te = _fact_value(facts, te_uuid, "CY", scope) or 0.0
                owners = _fact_value(facts, owners_uuid, "CY", scope) or 0.0
                nci = _fact_value(facts, nci_uuid, "CY", scope) or 0.0
                computed_totals[f"total_equity_cy{sfx}"] = te
                computed_totals[f"equity_owners_cy{sfx}"] = owners
                computed_totals[f"equity_nci_cy{sfx}"] = nci
                expected = owners + nci
                if abs(te - expected) > _balance_tolerance(te, owners, nci):
                    is_balanced = False
                    mismatches.append(
                        f"{pfx}Total equity ({te}) != "
                        f"owners ({owners}) + non-controlling interests ({nci}) = {expected}"
                    )

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=matches_pdf,
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback="\n".join(feedback_lines),
        mandatory_unfilled=_collect_unfilled_mandatory_facts(
            nodes, facts, main, filing_level),
    )


# ---------------------------------------------------------------------------
# SOCF — cash at end == cash at beginning + net increase after FX
# ---------------------------------------------------------------------------


def _verify_socf_facts(
    nodes: list[dict], facts: dict, filing_level: str,
    pdf_values: Optional[dict[str, float]],
) -> VerificationResult:
    sheet = _sheet_present(nodes, ["SOCF-Indirect", "SOCF-Direct"])
    rows = _rows_on_sheet(nodes, sheet) if sheet else []

    computed_totals: dict[str, float] = {}
    mismatches: list[str] = []
    diagnostics: list[str] = []
    is_balanced = True

    # First-match-wins by label (mirrors setdefault), binding cash_end to the
    # statement-line LEAF, not the lower reconciliation formula row.
    rows_by_label: dict[str, str] = {}
    for n in rows:
        norm = _normalize_label(str(n["label"]))
        if "net cash flows" in norm and "operating" in norm:
            rows_by_label.setdefault("net_operating", n["uuid"])
        elif "net cash flows" in norm and "investing" in norm:
            rows_by_label.setdefault("net_investing", n["uuid"])
        elif "net cash flows" in norm and "financing" in norm:
            rows_by_label.setdefault("net_financing", n["uuid"])
        elif "net increase" in norm and "after" in norm:
            rows_by_label.setdefault("net_increase_after_fx", n["uuid"])
        elif "net increase" in norm and "before" in norm:
            rows_by_label.setdefault("net_increase_before_fx", n["uuid"])
        elif "cash and cash equivalents at end" in norm:
            rows_by_label.setdefault("cash_end", n["uuid"])
        elif "cash and cash equivalents at beginning" in norm:
            rows_by_label.setdefault("cash_beginning", n["uuid"])

    required = ["net_operating", "net_increase_before_fx"]
    missing = [k for k in required if k not in rows_by_label]
    if missing:
        return VerificationResult(
            is_balanced=False,
            matches_pdf=_check_pdf_values({}, pdf_values),
            computed_totals={},
            pdf_values=pdf_values or {},
            mismatches=[f"Required SOCF rows not found: {', '.join(missing)}"],
            feedback=f"SOCF verification failed: missing rows {', '.join(missing)}",
        )

    for scope, prefix in _cy_scopes(filing_level):
        pfx = f"{prefix} " if prefix else ""
        sfx = f"_{prefix.lower()}" if prefix else ""

        col_totals: dict[str, float] = {}
        for key in ["net_operating", "net_investing", "net_financing", "net_increase_before_fx"]:
            if key in rows_by_label:
                col_totals[key] = _fact_value(facts, rows_by_label[key], "CY", scope) or 0.0
                computed_totals[f"{key}{sfx}"] = col_totals[key]

        if all(k in col_totals for k in ["net_operating", "net_investing", "net_financing", "net_increase_before_fx"]):
            expected = col_totals["net_operating"] + col_totals["net_investing"] + col_totals["net_financing"]
            actual = col_totals["net_increase_before_fx"]
            if abs(actual - expected) > _balance_tolerance(
                actual, col_totals["net_operating"],
                col_totals["net_investing"], col_totals["net_financing"],
            ):
                is_balanced = False
                mismatches.append(
                    f"{pfx}Net increase before FX ({actual}) != "
                    f"Operating ({col_totals['net_operating']}) + "
                    f"Investing ({col_totals['net_investing']}) + "
                    f"Financing ({col_totals['net_financing']}) = {expected}"
                )
                hint = _imbalance_diagnostic(actual - expected, {
                    f"{pfx}Net cash from operating activities": col_totals["net_operating"],
                    f"{pfx}Net cash from investing activities": col_totals["net_investing"],
                    f"{pfx}Net cash from financing activities": col_totals["net_financing"],
                })
                if hint:
                    diagnostics.append(hint)

        for key in ["cash_beginning", "cash_end", "net_increase_after_fx"]:
            if key in rows_by_label:
                col_totals[key] = _fact_value(facts, rows_by_label[key], "CY", scope) or 0.0
                computed_totals[f"{key}{sfx}"] = col_totals[key]

        if all(k in col_totals for k in ["cash_beginning", "cash_end", "net_increase_after_fx"]):
            expected = col_totals["cash_beginning"] + col_totals["net_increase_after_fx"]
            actual = col_totals["cash_end"]
            if abs(actual - expected) > _balance_tolerance(
                actual, col_totals["cash_beginning"], col_totals["net_increase_after_fx"],
            ):
                is_balanced = False
                mismatches.append(
                    f"{pfx}Cash at end ({actual}) != "
                    f"Cash at beginning ({col_totals['cash_beginning']}) + "
                    f"Net increase after FX ({col_totals['net_increase_after_fx']}) = {expected}"
                )
                hint = _imbalance_diagnostic(actual - expected, {
                    f"{pfx}Cash and cash equivalents at beginning": col_totals["cash_beginning"],
                    f"{pfx}Net increase after FX": col_totals["net_increase_after_fx"],
                })
                if hint:
                    diagnostics.append(hint)

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=_check_pdf_values(computed_totals, pdf_values),
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback=_compose_feedback(
            mismatches, is_balanced, "SOCF balance check passed.",
            diagnostics=diagnostics,
        ),
        mandatory_unfilled=_collect_unfilled_mandatory_facts(
            nodes, facts, sheet, filing_level) if sheet else [],
    )


# ---------------------------------------------------------------------------
# SOPL — profit == owners + NCI attribution
# ---------------------------------------------------------------------------


def _verify_sopl_facts(
    nodes: list[dict], facts: dict, filing_level: str,
    pdf_values: Optional[dict[str, float]],
) -> VerificationResult:
    sheet = _sheet_present(nodes, ["SOPL-Function", "SOPL-Nature"])
    rows = _rows_on_sheet(nodes, sheet) if sheet else []

    computed_totals: dict[str, float] = {}
    mismatches: list[str] = []
    diagnostics: list[str] = []
    is_balanced = True

    profit_loss_uuid = None
    total_profit_uuid = None
    last_profit_loss_uuid = None
    for n in rows:
        norm = _normalize_label(str(n["label"]))
        if norm == "profit (loss)":
            if profit_loss_uuid is None:
                profit_loss_uuid = n["uuid"]
            last_profit_loss_uuid = n["uuid"]
        elif norm == "total profit (loss)":
            total_profit_uuid = n["uuid"]

    if total_profit_uuid is None and last_profit_loss_uuid and last_profit_loss_uuid != profit_loss_uuid:
        total_profit_uuid = last_profit_loss_uuid

    if not profit_loss_uuid:
        return VerificationResult(
            is_balanced=False,
            matches_pdf=_check_pdf_values({}, pdf_values),
            computed_totals={},
            pdf_values=pdf_values or {},
            mismatches=["Required label not found: 'Profit (loss)'"],
            feedback="SOPL verification failed: missing 'Profit (loss)' label",
        )

    owners_uuid = nci_uuid = None
    if filing_level == "group":
        for n in rows:
            norm = _normalize_label(str(n["label"]))
            if "profit" in norm and "owners of parent" in norm and "attribut" in norm:
                owners_uuid = n["uuid"]
            elif "profit" in norm and "non-controlling interest" in norm and "attribut" in norm:
                nci_uuid = n["uuid"]

    for scope, prefix in _cy_scopes(filing_level):
        pfx = f"{prefix} " if prefix else ""
        sfx = f"_{prefix.lower()}" if prefix else ""

        pl_val = _fact_value(facts, profit_loss_uuid, "CY", scope) or 0.0
        computed_totals[f"profit_loss_cy{sfx}"] = pl_val

        if total_profit_uuid:
            attr_val = _fact_value(facts, total_profit_uuid, "CY", scope) or 0.0
            computed_totals[f"total_profit_attribution_cy{sfx}"] = attr_val
            if abs(pl_val - attr_val) > _balance_tolerance(pl_val, attr_val):
                is_balanced = False
                mismatches.append(
                    f"{pfx}Profit/loss ({pl_val}) != attribution total ({attr_val})"
                )

        if filing_level == "group" and owners_uuid and nci_uuid:
            owners = _fact_value(facts, owners_uuid, "CY", scope) or 0.0
            nci = _fact_value(facts, nci_uuid, "CY", scope) or 0.0
            computed_totals[f"profit_owners_cy{sfx}"] = owners
            computed_totals[f"profit_nci_cy{sfx}"] = nci
            expected = owners + nci
            if abs(pl_val - expected) > _balance_tolerance(pl_val, owners, nci):
                is_balanced = False
                mismatches.append(
                    f"{pfx}Profit/loss ({pl_val}) != "
                    f"owners ({owners}) + non-controlling interests ({nci}) = {expected}"
                )
                hint = _imbalance_diagnostic(pl_val - expected, {
                    f"{pfx}Profit (loss), attributable to owners of parent": owners,
                    f"{pfx}Profit (loss), attributable to non-controlling interests": nci,
                })
                if hint:
                    diagnostics.append(hint)

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=_check_pdf_values(computed_totals, pdf_values),
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback=_compose_feedback(
            mismatches, is_balanced, "SOPL attribution check passed.",
            diagnostics=diagnostics,
        ),
        mandatory_unfilled=_collect_unfilled_mandatory_facts(
            nodes, facts, sheet, filing_level) if sheet else [],
    )


# ---------------------------------------------------------------------------
# SOCI — total comprehensive income == P&L + OCI; attribution check
# ---------------------------------------------------------------------------


def _verify_soci_facts(
    nodes: list[dict], facts: dict, filing_level: str,
    pdf_values: Optional[dict[str, float]],
) -> VerificationResult:
    sheet = _sheet_present(nodes, ["SOCI-BeforeOfTax", "SOCI-NetOfTax"])
    rows = _rows_on_sheet(nodes, sheet) if sheet else []

    computed_totals: dict[str, float] = {}
    mismatches: list[str] = []
    diagnostics: list[str] = []
    is_balanced = True

    pl_uuid = None
    total_oci_uuid = None
    total_ci_uuids: list[str] = []
    for n in rows:
        norm = _normalize_label(str(n["label"]))
        if norm == "profit (loss)" and pl_uuid is None:
            pl_uuid = n["uuid"]
        elif norm == "total other comprehensive income" and total_oci_uuid is None:
            total_oci_uuid = n["uuid"]
        elif norm == "total comprehensive income":
            total_ci_uuids.append(n["uuid"])

    if not pl_uuid or not total_ci_uuids:
        missing = []
        if not pl_uuid:
            missing.append("'Profit (loss)'")
        if not total_ci_uuids:
            missing.append("'Total comprehensive income'")
        return VerificationResult(
            is_balanced=False,
            matches_pdf=_check_pdf_values({}, pdf_values),
            computed_totals={},
            pdf_values=pdf_values or {},
            mismatches=[f"Required label not found: {', '.join(missing)}"],
            feedback=f"SOCI verification failed: missing labels {', '.join(missing)}",
        )

    for scope, prefix in _cy_scopes(filing_level):
        pfx = f"{prefix} " if prefix else ""
        sfx = f"_{prefix.lower()}" if prefix else ""

        pl_val = _fact_value(facts, pl_uuid, "CY", scope) or 0.0
        computed_totals[f"profit_loss_cy{sfx}"] = pl_val

        oci_val = None
        if total_oci_uuid:
            oci_val = _fact_value(facts, total_oci_uuid, "CY", scope) or 0.0
            computed_totals[f"total_oci_cy{sfx}"] = oci_val

        if total_ci_uuids:
            ci_val = _fact_value(facts, total_ci_uuids[0], "CY", scope) or 0.0
            computed_totals[f"total_comprehensive_income_cy{sfx}"] = ci_val
            if oci_val is not None:
                expected = pl_val + oci_val
                if abs(ci_val - expected) > _balance_tolerance(ci_val, pl_val, oci_val):
                    is_balanced = False
                    mismatches.append(
                        f"{pfx}Total CI ({ci_val}) != P&L ({pl_val}) "
                        f"+ OCI ({oci_val}) = {expected}"
                    )
                    hint = _imbalance_diagnostic(ci_val - expected, {
                        f"{pfx}Total other comprehensive income": oci_val,
                        f"{pfx}Profit (loss)": pl_val,
                    })
                    if hint:
                        diagnostics.append(hint)

        if len(total_ci_uuids) >= 2:
            ci_main = _fact_value(facts, total_ci_uuids[0], "CY", scope) or 0.0
            ci_attr = _fact_value(facts, total_ci_uuids[1], "CY", scope) or 0.0
            computed_totals[f"total_ci_attribution_cy{sfx}"] = ci_attr
            if abs(ci_main - ci_attr) > _balance_tolerance(ci_main, ci_attr):
                is_balanced = False
                mismatches.append(
                    f"{pfx}Total CI ({ci_main}) != attribution total ({ci_attr})"
                )

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=_check_pdf_values(computed_totals, pdf_values),
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback=_compose_feedback(
            mismatches, is_balanced, "SOCI balance check passed.",
            diagnostics=diagnostics,
        ),
        mandatory_unfilled=_collect_unfilled_mandatory_facts(
            nodes, facts, sheet, filing_level) if sheet else [],
    )


# ---------------------------------------------------------------------------
# SOCIE — closing equity == restated opening + total increase (matrix)
# ---------------------------------------------------------------------------


def _verify_socie_facts(
    nodes: list[dict], facts: dict, variant: str, filing_level: str,
    filing_standard: str, pdf_values: Optional[dict[str, float]],
) -> VerificationResult:
    from cross_checks.facts_util import socie_total_col

    sheet = "SOCIE" if any(n["sheet"] == "SOCIE" for n in nodes) else (
        nodes[0]["sheet"] if nodes else "SOCIE")

    computed_totals: dict[str, float] = {}
    mismatches: list[str] = []
    diagnostics: list[str] = []
    is_balanced = True

    is_sore = (variant or "").strip().lower() == "sore" or sheet.lower() == "sore"
    if is_sore:
        restated_label = "retained earnings at beginning of period, restated"
        total_label = "total increase (decrease) in retained earnings"
        closing_label = "retained earnings at end of period"
        pretty = ("'Retained earnings at beginning of period, restated'",
                  "'Total increase (decrease) in retained earnings'",
                  "'Retained earnings at end of period'")
    else:
        restated_label = "equity at beginning of period, restated"
        total_label = "total increase (decrease) in equity"
        closing_label = "equity at end of period"
        pretty = ("'Equity at beginning of period, restated'",
                  "'Total increase (decrease) in equity'",
                  "'Equity at end of period'")

    total_col = socie_total_col(filing_standard)  # 'X' (MFRS) / 'B' (MPERS)

    have_restated = _matrix_label_exists(nodes, sheet, restated_label, total_col)
    have_total = _matrix_label_exists(nodes, sheet, total_label, total_col)
    have_closing = _matrix_label_exists(nodes, sheet, closing_label, total_col)
    if not have_restated or not have_total or not have_closing:
        missing = []
        if not have_restated:
            missing.append(pretty[0])
        if not have_total:
            missing.append(pretty[1])
        if not have_closing:
            missing.append(pretty[2])
        return VerificationResult(
            is_balanced=False,
            matches_pdf=_check_pdf_values({}, pdf_values),
            computed_totals={},
            pdf_values=pdf_values or {},
            mismatches=[f"Required label not found: {', '.join(missing)}"],
            feedback=f"SOCIE verification failed: missing labels {', '.join(missing)}",
        )

    if filing_level == "group":
        blocks = [
            ("group_cy", "CY", "Group"), ("group_py", "PY", "Group"),
            ("company_cy", "CY", "Company"), ("company_py", "PY", "Company"),
        ]
    else:
        blocks = [("cy", "CY", "Company"), ("py", "PY", "Company")]

    for label, period, scope in blocks:
        restated = _matrix_value(nodes, facts, sheet, restated_label, total_col, period, scope) or 0.0
        increase = _matrix_value(nodes, facts, sheet, total_label, total_col, period, scope) or 0.0
        closing = _matrix_value(nodes, facts, sheet, closing_label, total_col, period, scope) or 0.0

        computed_totals[f"restated_equity_{label}"] = restated
        computed_totals[f"total_increase_{label}"] = increase
        computed_totals[f"closing_equity_{label}"] = closing

        expected = restated + increase
        diff = closing - expected
        if abs(diff) > _balance_tolerance(closing, restated, increase):
            is_balanced = False
            mismatches.append(
                f"{label}: closing equity ({closing}) != "
                f"restated ({restated}) + total increase ({increase}) = {expected}"
            )
            hint = _imbalance_diagnostic(diff, {
                f"{label} restated opening equity": restated,
                f"{label} total increase (decrease) in equity": increase,
            })
            if hint:
                diagnostics.append(hint)

    return VerificationResult(
        is_balanced=is_balanced,
        matches_pdf=_check_pdf_values(computed_totals, pdf_values),
        computed_totals=computed_totals,
        pdf_values=pdf_values or {},
        mismatches=mismatches,
        feedback=_compose_feedback(
            mismatches, is_balanced, "SOCIE balance check passed.",
            diagnostics=diagnostics,
        ),
        mandatory_unfilled=_collect_unfilled_mandatory_socie_facts(
            nodes, facts, sheet, filing_level, filing_standard),
    )


# ---------------------------------------------------------------------------
# Mandatory scans (STRICTER than xlsx — product decision 2026-06-14)
# ---------------------------------------------------------------------------


def _collect_unfilled_mandatory_facts(
    nodes: list[dict], facts: dict, sheet: str, filing_level: str,
) -> list[str]:
    """Linear-statement mandatory scan. A mandatory (``*``) row is unfilled when
    its concept carries NO fact in EVERY required CY scope. ``not_disclosed`` =
    resolved (a present fact). COMPUTED rows (formula totals) are skipped — the
    cascade derives them, exactly as the xlsx path treats their formula cell as
    "filled". See module docstring for the parity caveat."""
    scopes = ["Group", "Company"] if filing_level == "group" else ["Company"]
    unfilled: list[str] = []
    for n in nodes:
        if n["sheet"] != sheet:
            continue
        label = str(n["label"]).strip()
        if not label.startswith("*"):
            continue
        if n["kind"] == "COMPUTED":
            continue
        if any(facts.get((n["uuid"], "CY", scope)) is None for scope in scopes):
            unfilled.append(label)
    return unfilled


def _collect_unfilled_mandatory_socie_facts(
    nodes: list[dict], facts: dict, sheet: str, filing_level: str,
    filing_standard: str,
) -> list[str]:
    """SOCIE mandatory scan. The MFRS matrix row is filled if ANY of its matrix
    cells carries a fact in the required scope(s) — the row's data can sit in
    any equity-component column (mirrors the xlsx ``is_mfrs_socie_matrix``
    any-column rule). MPERS SoRE's flat layout reuses the linear scan."""
    is_mfrs_matrix = filing_standard == "mfrs"
    if not is_mfrs_matrix:
        return _collect_unfilled_mandatory_facts(nodes, facts, sheet, filing_level)

    scopes = ["Group", "Company"] if filing_level == "group" else ["Company"]
    periods = ["CY", "PY"]
    # Group matrix cells by (label, render_row) — one logical row per render_row.
    # A mandatory row is unfilled when NO matrix cell on it has a fact in ANY
    # required scope/period.
    unfilled: list[str] = []
    seen_rows: set[int] = set()
    for n in nodes:
        if n["sheet"] != sheet or n["matrix_col"] is None:
            continue
        label = str(n["label"]).strip()
        if not label.startswith("*"):
            continue
        row = n["row"]
        if row in seen_rows:
            continue
        seen_rows.add(row)
        # All matrix cells on this render_row.
        row_cells = [
            m for m in nodes
            if m["sheet"] == sheet and m["row"] == row and m["matrix_col"] is not None
        ]
        filled = any(
            facts.get((m["uuid"], p, s)) is not None
            for m in row_cells for p in periods for s in scopes
        )
        if not filled:
            unfilled.append(label)
    return unfilled


def _scan_magnitude_warnings_facts(
    nodes: list[dict], facts: dict, filing_level: str,
) -> list[str]:
    """Fact-space magnitude scan — twin of ``verifier._scan_magnitude_warnings``.

    Walks DATA-ENTRY leaves (COMPUTED totals are formula cells the xlsx path
    skips), comparing the CY/PY pair per entity scope. Same threshold, message,
    and first-N/overflow cap. Ordering follows (sheet, render_row)."""
    warnings: list[str] = []
    scope_pairs = (
        [("Group", "Group"), ("Company", "Company")]
        if filing_level == "group"
        else [("Company", "")]
    )
    for n in nodes:
        if n["kind"] == "COMPUTED":
            continue
        raw_label = str(n["label"]).strip().lstrip("*").strip()
        if not raw_label:
            continue
        for scope, pfx in scope_pairs:
            cy = _fact_value(facts, n["uuid"], "CY", scope)
            py = _fact_value(facts, n["uuid"], "PY", scope)
            if cy is None or py is None:
                continue
            cy_f, py_f = float(cy), float(py)
            if cy_f == 0 or py_f == 0:
                continue
            if (cy_f > 0) != (py_f > 0):
                continue
            if abs(cy_f) < _MAGNITUDE_MIN_VALUE or abs(py_f) < _MAGNITUDE_MIN_VALUE:
                continue
            if abs(math.log10(abs(cy_f) / abs(py_f))) >= _MAGNITUDE_LOG10_THRESHOLD:
                scope_pfx = f"{pfx} " if pfx else ""
                warnings.append(
                    f"{scope_pfx}'{raw_label}': CY ({cy_f:,.0f}) is ~"
                    f"{abs(cy_f) / abs(py_f):,.0f}x PY ({py_f:,.0f}) — check "
                    f"the statement's thousands/millions header (possible "
                    f"scale-unit error)."
                )
    if len(warnings) > _MAGNITUDE_MAX_WARNINGS:
        overflow = len(warnings) - _MAGNITUDE_MAX_WARNINGS
        warnings = warnings[:_MAGNITUDE_MAX_WARNINGS]
        warnings.append(
            f"... and {overflow} more rows show the same pattern — this "
            f"strongly suggests a statement-wide scale-unit error (verify "
            f"the statement header's stated unit)."
        )
    return warnings
