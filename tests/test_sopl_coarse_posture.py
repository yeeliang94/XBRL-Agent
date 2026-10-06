"""Rendered SOPL policy: supported reconciliation, bounded fallback, no plugs."""
import pytest
from prompts import render_prompt
from statement_types import StatementType


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
@pytest.mark.parametrize("variant", ["Function", "Nature"])
@pytest.mark.parametrize("level", ["company", "group"])
def test_sopl_rendered_reconciliation_contract(standard, variant, level):
    prompt = render_prompt(StatementType.SOPL, variant,
                           filing_standard=standard, filing_level=level)
    flat = " ".join(prompt.split())
    assert "complete disclosed components reconcile exactly" in flat
    assert "each period and entity scope independently" in flat
    assert "Never retain the full face amount alongside its components" in flat
    assert "report the unresolved breakdown/classification" in flat
    assert "NEVER invent a balancing, residual or unanalysed amount" in flat
    assert "dedicated face-first workflow" in flat
    assert "ACCOUNTANT EXTRACTION PROCEDURE" not in prompt
    assert "invent a split from principal activity alone" in flat
    assert "even if the financials show a breakdown" not in prompt
    assert "PRINCIPAL ACTIVITY" in prompt or "principal activity disclosure" in prompt
    if standard == "mfrs":
        assert "Revenue from rendering of other services" in prompt
        assert "MPERS REVENUE BUCKET" not in prompt
    else:
        assert "Other revenue from rendering of services" in prompt
        assert "MFRS REVENUE CLASSIFICATION" not in prompt
