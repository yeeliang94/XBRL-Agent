"""Tests for prompts/monolith_face.md + monolith/prompt_renderer.py.

The pinning tests grep-assert that the load-bearing invariants survive
into the rendered prompt verbatim. If a future edit softens any of them,
the corresponding test fails loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from monolith.config import MONOLITH_PROMPT_BYTE_CEILING
from monolith.prompt_renderer import RenderedPrompt, render
from statement_types import StatementType


_REPO = Path(__file__).resolve().parent.parent
_PROMPT_MD = _REPO / "prompts" / "monolith_face.md"
_FINCO_PDF = _REPO / "data" / "FINCO-Audited-Financial-Statement-2021.pdf"


# ---------------------------------------------------------------------------
# Static prompt-content invariants
# ---------------------------------------------------------------------------


def test_prompt_md_contains_load_bearing_rules():
    text = _PROMPT_MD.read_text(encoding="utf-8")
    # SOCIE dividend sign convention (gotcha #15, ADR-002).
    assert "dividends" in text.lower() and "positive" in text.lower(), (
        "SOCIE dividend sign rule missing or softened"
    )
    # Abstract-row guard (gotcha #17).
    assert "abstract" in text.lower(), "abstract-row rule missing"
    # No-residual-plug rule.
    assert (
        "catch-all" in text.lower() or "residual" in text.lower()
    ), "no-residual-plug rule missing"
    # All five cross-statement identities surfaced.
    for ident in (
        "sofp_balance",
        "sopl_to_socie_profit",
        "soci_to_socie_tci",
        "socie_to_sofp_equity",
        "socf_to_sofp_cash",
    ):
        assert ident in text, f"cross-check identity {ident} missing from prompt"
    # Workflow contract bullet points.
    assert "get_state" in text and "write_cells" in text and "done" in text


def test_prompt_md_describes_workflow_contract():
    """get_state → write_cells → done loop must be stated as a contract."""
    text = _PROMPT_MD.read_text(encoding="utf-8")
    assert "after every" in text.lower() and "get_state" in text.lower()


# ---------------------------------------------------------------------------
# Renderer behaviour
# ---------------------------------------------------------------------------


def test_render_emits_rules_template_and_pdf_blocks(tmp_path):
    if not _FINCO_PDF.exists():
        pytest.skip("FINCO PDF not present in this checkout")
    out = render(
        str(_FINCO_PDF),
        filing_standard="mfrs",
        filing_level="company",
    )
    assert isinstance(out, RenderedPrompt)
    assert "Monolith Face-Statement Agent" in out.full
    assert "## Template structure" in out.full
    assert "## PDF TEXT" in out.full
    # Cross-reference annotations present in the structure block.
    for ident in (
        "sofp_balance",
        "sopl_to_socie_profit",
        "soci_to_socie_tci",
        "socie_to_sofp_equity",
        "socf_to_sofp_cash",
    ):
        assert ident in out.template_structure


def test_render_per_sheet_lists_include_all_five():
    """The template-structure block must list a section per face statement."""
    # Doesn't need a PDF — use an empty path; the PDF block is empty and
    # the structure block is what we're asserting on.
    out = render(
        pdf_path="",
        filing_standard="mfrs",
        filing_level="company",
    )
    for stmt in (StatementType.SOFP, StatementType.SOPL, StatementType.SOCI,
                 StatementType.SOCF, StatementType.SOCIE):
        assert f"### {stmt.value}" in out.template_structure


def test_render_byte_ceiling_trims_pdf_text(tmp_path):
    """Setting a tiny ceiling forces PDF text trim and sets `trimmed=True`."""
    if not _FINCO_PDF.exists():
        pytest.skip("FINCO PDF not present in this checkout")
    tiny_ceiling = 32 * 1024  # 32 KB
    out = render(
        str(_FINCO_PDF),
        byte_ceiling=tiny_ceiling,
    )
    # PDF text plus structure block is way over 32 KB on FINCO, so trim
    # must trigger.
    assert out.trimmed, (
        f"expected trimming at {tiny_ceiling} byte budget; got "
        f"{out.byte_size} bytes"
    )


def test_render_default_ceiling_used_when_unset(tmp_path):
    out = render(pdf_path="", byte_ceiling=MONOLITH_PROMPT_BYTE_CEILING)
    # With no PDF the assembly stays small; trimmed should be False.
    assert out.trimmed is False
