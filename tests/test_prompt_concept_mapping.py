"""RUN-REVIEW P1-3 (2026-04-26): pin the AFS↔SSM concept cheat-sheet.

The Amway run mis-routed `Consumer products` (an AFS term for finished
goods) into `Other inventories`, and put a subsidiary trade receivable
into `Other receivables due from subsidiaries` instead of the trade row.
The cheat-sheet in `prompts/sofp.md` is the prompt-layer fix; this test
ensures the load-bearing entries don't get accidentally edited away.

The cheat-sheet lives in the shared `sofp.md` (not the MFRS/MPERS
precedence tier) because the SSM labels are identical across standards
— see _build_fixtures.py and the row maps in
`tests/fixtures/run_review/`. The only divergence (`Refunds provision`
exists on MFRS, not on MPERS) is called out inline in the cheat-sheet.
"""
from __future__ import annotations

from pathlib import Path

from prompts import render_prompt
from statement_types import StatementType

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def test_sofp_cheatsheet_present_in_static_prompt() -> None:
    """The cheat-sheet header and load-bearing entries must be in
    `prompts/sofp.md` directly. A casual rewrite that drops the
    `=== AFS NOTE → SSM ROW MAPPING` anchor will fail this test."""
    text = (_PROMPTS / "sofp.md").read_text(encoding="utf-8")
    lower = text.lower()
    assert "afs note → ssm row mapping" in lower, (
        "sofp.md must carry the named '=== AFS NOTE → SSM ROW MAPPING ==='"
        " section — that's the cheat-sheet anchor"
    )
    # Load-bearing entries from RUN-REVIEW §3.3
    assert "consumer products" in lower
    assert "finished goods" in lower
    assert "trade receivables due from subsidiary" in lower
    # The trade-vs-other axis explanation (the *why*, not just *what*)
    assert "trade vs other" in lower or "trade vs. other" in lower
    # Provisions guidance (warranty / refunds were lumped into Other in
    # the reviewed run)
    assert "warranty provision" in lower
    assert "do not lump these" in lower or "do not lump" in lower


def test_cheatsheet_renders_for_both_filing_standards() -> None:
    """Because the cheat-sheet lives in the shared `sofp.md`, MPERS runs
    fall through the prompt-precedence chain to the same content. This
    test makes that intentional behaviour explicit so a future split
    into `sofp_mfrs.md` / `sofp_mpers.md` doesn't silently regress
    MPERS coverage of the cheat-sheet."""
    mfrs_prompt = render_prompt(
        StatementType.SOFP, variant="CuNonCu",
        filing_level="company", filing_standard="mfrs",
    )
    mpers_prompt = render_prompt(
        StatementType.SOFP, variant="CuNonCu",
        filing_level="company", filing_standard="mpers",
    )
    for rendered in (mfrs_prompt, mpers_prompt):
        lower = rendered.lower()
        assert "afs note → ssm row mapping" in lower
        assert "consumer products" in lower
        assert "trade receivables due from subsidiary" in lower


def test_cheatsheet_documents_mpers_refunds_divergence() -> None:
    """The single MFRS-vs-MPERS divergence (no separate `Refunds
    provision` row in MPERS) is called out inline so the agent knows
    why the row may be missing on MPERS runs. Pinned because dropping
    the call-out silently re-opens the lump-into-Other failure mode
    on MPERS."""
    text = (_PROMPTS / "sofp.md").read_text(encoding="utf-8").lower()
    assert "mpers" in text and "refunds provision" in text, (
        "sofp.md must mention that MPERS lacks a separate Refunds "
        "provision row — without that, an MPERS agent runs blind"
    )
