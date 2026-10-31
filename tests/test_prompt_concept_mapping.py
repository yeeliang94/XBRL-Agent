"""Pin the shared classification principles that replaced the SOFP cheat-sheet.

RUN-REVIEW P1-3 (2026-04-26): the Amway run mis-routed `Consumer products`
into `Other inventories` and a subsidiary trade receivable into the
`Other receivables` row. The first fix was a per-label cheat-sheet in
`prompts/sofp.md`. It was replaced (2026-10-31) by general principles in
`prompts/_base.md`: classify by substance (trade versus other turns on what
the item is, not the counterparty), follow the source's own classification,
and prefer a dedicated row over a catch-all `Other …` row. These cover both
original errors without a label list, and reach every face statement.
"""
from __future__ import annotations

import pytest

from prompts import render_prompt
from statement_types import StatementType


@pytest.mark.parametrize("standard", ["mfrs", "mpers"])
@pytest.mark.parametrize("statement,variant", [
    (StatementType.SOFP, "CuNonCu"),
    (StatementType.SOFP, "OrderOfLiquidity"),
    (StatementType.SOPL, "Function"),
    (StatementType.SOCF, "Indirect"),
])
def test_classification_principles_reach_every_face_statement(statement, variant, standard) -> None:
    rendered = render_prompt(
        statement, variant=variant, filing_level="company", filing_standard=standard,
    ).lower()
    assert "=== classification principles ===" in rendered
    # The Amway trade-receivable error: substance, not counterparty.
    assert "classify by substance" in rendered
    assert "not on whether the counterparty is related" in rendered
    # The Amway inventories error: dedicated row before the catch-all.
    assert "prefer the most specific suitable row" in rendered
    assert "follow the source's own classification" in rendered
    assert "lookup_definitions" in rendered
    # The per-label cheat-sheet is gone; principles, not a rulebook.
    assert "afs note → ssm row mapping" not in rendered
    assert "consumer products" not in rendered
