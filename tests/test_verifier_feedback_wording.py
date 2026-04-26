"""Bug B (2026-04-26) — verifier imbalance feedback must not push the agent
into a catch-all plug.

Old feedback wording was directive: "Action: equity+liabilities section is
too low. Re-examine liabilities or equity sub-items." Combined with the
hard save-gate that refused finalisation while imbalanced, this told the
agent to "fix one side" — and the path of least resistance is to plug a
residual into a catch-all row.

The new wording keeps the diagnostic detail (which side is high/low, which
sub-items to re-examine) but explicitly tells the agent that finishing
with an imbalance is acceptable and that plugging a catch-all is not.

Peer-review #2 + #3 (2026-04-26): the no-plug guidance must apply to ALL
imbalance branches — CY, PY, and (on Group filings) the Company-CY /
Company-PY columns. The original implementation only updated CY, leaving
the agent with no anti-plug guard on the other three branches. These
parametrised tests pin all four.
"""
from __future__ import annotations

import openpyxl
import pytest

from tools.verifier import verify_totals


def _no_plug_clause_present(feedback: str) -> bool:
    f = feedback.lower()
    return any(s in f for s in (
        "do not plug",
        "never plug",
        "do not use a catch-all",
        "do not invent a residual",
    ))


def _leave_gap_clause_present(feedback: str) -> bool:
    f = feedback.lower()
    return any(s in f for s in ("leave", "honest", "finish"))


def _make_sofp_with_imbalance(
    tmp_path,
    *,
    cy_imbalanced: bool,
    py_imbalanced: bool,
    company_cy_imbalanced: bool = False,
    company_py_imbalanced: bool = False,
) -> str:
    """Build a SOFP workbook with imbalances on selected period branches.

    Group columns (D=Company CY, E=Company PY) are populated only when the
    matching `company_*_imbalanced` flag is set so company-only tests don't
    accidentally trip the standalone CY/PY checks.
    """
    path = tmp_path / "imbalanced_sofp.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOFP-CuNonCu"
    ws["A1"] = "Total assets"
    ws["A5"] = "Total equity and liabilities"

    # Group + Company CY (cols B / D), prior year (cols C / E).
    ws["B1"] = 450 if cy_imbalanced else 400
    ws["B5"] = 400
    ws["C1"] = 700 if py_imbalanced else 600
    ws["C5"] = 600

    if company_cy_imbalanced or company_py_imbalanced:
        ws["D1"] = 350 if company_cy_imbalanced else 300
        ws["D5"] = 300
        ws["E1"] = 525 if company_py_imbalanced else 500
        ws["E5"] = 500

    wb.save(str(path))
    return str(path)


def test_imbalance_direction_wording_for_negative_diff(tmp_path):
    """Peer-review 2026-04-26: when assets < equity+liabilities (diff < 0),
    the diagnostic must say 'assets section is lower'. The legacy phrasing
    said 'assets is higher', which inverts which side the agent should
    re-examine and effectively points it at the wrong notes.
    """
    path = tmp_path / "assets_lower.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOFP-CuNonCu"
    ws["A1"] = "Total assets"
    ws["B1"] = 400  # lower
    ws["A5"] = "Total equity and liabilities"
    ws["B5"] = 450  # higher → diff < 0
    wb.save(str(path))

    result = verify_totals(str(path))
    feedback = (result.feedback or "").lower()
    assert "assets section is lower" in feedback, (
        f"diff < 0 must say 'assets section is lower'. Got: {result.feedback!r}"
    )
    # Sanity: the wrong phrasing must not appear.
    assert "assets section is higher" not in feedback


def test_imbalance_direction_wording_for_positive_diff(tmp_path):
    """Mirror of the negative-diff test — when diff > 0, the diagnostic
    must point at equity+liabilities being lower (the side the agent
    should re-examine for a missing component)."""
    path = tmp_path / "equity_lower.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOFP-CuNonCu"
    ws["A1"] = "Total assets"
    ws["B1"] = 500  # higher
    ws["A5"] = "Total equity and liabilities"
    ws["B5"] = 400  # lower → diff > 0
    wb.save(str(path))

    result = verify_totals(str(path))
    feedback = (result.feedback or "").lower()
    assert "equity+liabilities section is lower" in feedback, (
        f"diff > 0 must say 'equity+liabilities section is lower'. "
        f"Got: {result.feedback!r}"
    )
    assert "equity+liabilities section is higher" not in feedback


@pytest.mark.parametrize("scenario", [
    pytest.param(
        {"cy_imbalanced": True, "py_imbalanced": False, "filing_level": "company"},
        id="cy_only",
    ),
    pytest.param(
        {"cy_imbalanced": False, "py_imbalanced": True, "filing_level": "company"},
        id="py_only",
    ),
    pytest.param(
        {"cy_imbalanced": True, "py_imbalanced": True, "filing_level": "company"},
        id="cy_and_py",
    ),
    pytest.param(
        {
            "cy_imbalanced": False, "py_imbalanced": False,
            "company_cy_imbalanced": True, "company_py_imbalanced": False,
            "filing_level": "group",
        },
        id="group_company_cy_only",
    ),
    pytest.param(
        {
            "cy_imbalanced": False, "py_imbalanced": False,
            "company_cy_imbalanced": False, "company_py_imbalanced": True,
            "filing_level": "group",
        },
        id="group_company_py_only",
    ),
])
def test_imbalance_feedback_is_non_directive_on_every_branch(scenario, tmp_path):
    """Pin: any imbalance — CY, PY, Company CY, or Company PY — must produce
    feedback that (a) names the imbalance, (b) forbids plugging, and
    (c) tells the agent it's okay to finish with the gap."""
    filing_level = scenario.pop("filing_level")
    path = _make_sofp_with_imbalance(tmp_path, **scenario)

    result = verify_totals(path, filing_level=filing_level)

    assert result.is_balanced is False, "test setup error — should be unbalanced"
    feedback = result.feedback or ""

    assert "imbalance" in feedback.lower(), (
        f"feedback must report the imbalance diagnostically. Got: {feedback!r}"
    )
    assert _no_plug_clause_present(feedback), (
        f"every imbalance branch must carry the no-plug clause. "
        f"Got: {feedback!r}"
    )
    assert _leave_gap_clause_present(feedback), (
        f"every imbalance branch must invite the agent to finish honestly. "
        f"Got: {feedback!r}"
    )
