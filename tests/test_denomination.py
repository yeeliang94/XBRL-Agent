"""Plan C — user-authoritative presentation denomination.

Pins:
- the face prompt renders the declared denomination as AUTHORITATIVE
  (transcribe verbatim, not "guess/VERIFY") for each scale;
- a scout-detected scale that disagrees with the declaration raises a loud
  reconciliation warning (the scout cross-check), and agreement does not;
- the axis threads through RunConfig / ExtractionDeps with a "thousands" default.
"""
from __future__ import annotations

import pytest

from statement_types import StatementType
from prompts import render_prompt, _render_denomination_block


# --------------------------------------------------------------------------
# Denomination block (authoritative wording + scout cross-check)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "denomination, expected_label",
    [
        ("thousands", "thousands (RM '000)"),
        ("millions", "millions (RM mil)"),
        ("units", "units (no scaling — values are reported as-is)"),
    ],
)
def test_denomination_block_renders_label_and_no_rescale(denomination, expected_label):
    block = _render_denomination_block(denomination)
    assert expected_label in block
    # The verbatim-transcription rule holds regardless of framing.
    assert "do NOT rescale" in block


def test_explicit_scale_is_authoritative():
    """units / millions can only come from a deliberate user choice (the
    toggle defaults to thousands), so they get the AUTHORITATIVE framing."""
    for denomination in ("units", "millions"):
        block = _render_denomination_block(denomination)
        assert "DECLARED BY FILER — AUTHORITATIVE" in block
        assert "AUTHORITATIVE" in block


def test_default_thousands_uses_verify_framing():
    """thousands is the toggle's default — it may be untouched, so it keeps the
    softer 'verify the header' nudge rather than claiming authority (soften
    default-only)."""
    block = _render_denomination_block("thousands")
    assert "DEFAULT — VERIFY" in block
    assert "VERIFY" in block
    assert "AUTHORITATIVE" not in block


def test_scout_disagreement_raises_warning():
    # Declared thousands, scout read millions → loud reconciliation warning.
    block = _render_denomination_block("thousands", scout_scale_unit="millions")
    assert "DISAGREES" in block
    assert "1000× error" in block


def test_scout_agreement_has_no_warning():
    block = _render_denomination_block("thousands", scout_scale_unit="thousands")
    assert "DISAGREES" not in block


def test_unknown_scout_scale_no_false_warning():
    # Scout couldn't determine the scale → no disagreement claim.
    block = _render_denomination_block("thousands", scout_scale_unit="unknown")
    assert "DISAGREES" not in block


# --------------------------------------------------------------------------
# Full face-prompt assembly
# --------------------------------------------------------------------------

def test_face_prompt_carries_declared_denomination():
    prompt = render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        denomination="millions",
    )
    assert "PRESENTATION DENOMINATION (DECLARED BY FILER" in prompt
    assert "millions (RM mil)" in prompt


def test_face_prompt_flags_scout_disagreement():
    prompt = render_prompt(
        statement_type=StatementType.SOFP,
        variant="CuNonCu",
        denomination="thousands",
        scout_context={"entity_name": "X", "scale_unit": "millions"},
    )
    assert "DISAGREES" in prompt


# --------------------------------------------------------------------------
# Threading defaults
# --------------------------------------------------------------------------

def test_runconfig_and_deps_default_to_thousands():
    from coordinator import RunConfig
    from extraction.agent import ExtractionDeps
    from token_tracker import TokenReport

    cfg = RunConfig(pdf_path="x.pdf", output_dir="/tmp")
    assert cfg.denomination == "thousands"

    deps = ExtractionDeps(
        pdf_path="x.pdf", template_path="t.xlsx", model="m", output_dir="/tmp",
        token_report=TokenReport(model="m"), statement_type=StatementType.SOFP,
        variant="CuNonCu",
    )
    assert deps.denomination == "thousands"
