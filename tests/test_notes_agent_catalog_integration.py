"""Phase 3 MPERS hardening — integration test proving the factory
seed + Phase 2 suffix normalisation compose to fix the run-#105 drop.

Red-green-refactor anchor for `docs/PLAN-mpers-notes-hardening.md`
Phase 3 Step 3.3. The test simulates the end-to-end path an MPERS
sub-agent takes:
  1. Factory loads the MPERS LIST_OF_NOTES label catalog.
  2. Agent emits a payload with a bare label (MFRS-style wording).
  3. Writer's label resolver matches it to the MPERS [text block] row.

Before Phase 2+3 this path silently rejected the payload. The
assertion below is the regression lock.
"""
from __future__ import annotations

from notes.agent import NotesDeps, create_notes_agent
from notes.writer import _resolve_row, _build_label_index
from notes_types import NotesTemplateType


def test_mpers_bare_label_resolves_against_seeded_catalog(tmp_path):
    """Simulates the run-#105 payload: agent emits
    `"Disclosure of other income"` (bare), MPERS template has
    `"Disclosure of other income [text block]"`. After Phase 2's
    suffix normalisation and Phase 3's catalog seed, the write
    pipeline must resolve the payload to the right row."""
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path="data/nonexistent.pdf",
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        filing_standard="mpers",
    )
    assert deps.template_label_catalog, (
        "Factory failed to seed the MPERS catalog — Phase 3 regression."
    )
    # Build the same label index the writer uses at write time.
    import openpyxl
    wb = openpyxl.load_workbook(deps.template_path, data_only=False)
    try:
        ws = wb[deps.sheet_name]
        idx = _build_label_index(ws)
    finally:
        wb.close()

    # The smoking-gun set from run-#105: labels the agent emitted that
    # the pre-fix pipeline silently dropped. Each must now resolve.
    canary = [
        "Disclosure of other income",
        "Disclosure of auditors' remuneration",
        "Disclosure of credit risk",
        "Disclosure of liquidity risk",
        "Disclosure of income tax expense",
    ]
    unresolved = []
    for label in canary:
        result = _resolve_row(idx, label)
        if result is None:
            unresolved.append(label)
    assert not unresolved, (
        "Phase 2+3 regression — these run-#105 labels still fail to "
        f"resolve against the MPERS template: {unresolved}"
    )


def test_mpers_concepts_absent_from_mpers_stay_unresolved(tmp_path):
    """Counter-test: MFRS-only concepts ('capital management', 'fair
    value measurement') genuinely do NOT exist in the MPERS taxonomy.
    Those must remain rejected — the fix isn't blanket acceptance,
    it's suffix equivalence. If these start passing, the normaliser
    has been weakened too far."""
    _, deps = create_notes_agent(
        template_type=NotesTemplateType.LIST_OF_NOTES,
        pdf_path="data/nonexistent.pdf",
        inventory=[],
        filing_level="company",
        model="test",
        output_dir=str(tmp_path),
        filing_standard="mpers",
    )
    import openpyxl
    wb = openpyxl.load_workbook(deps.template_path, data_only=False)
    try:
        ws = wb[deps.sheet_name]
        idx = _build_label_index(ws)
    finally:
        wb.close()
    # These concepts exist on MFRS but not MPERS — the agent emitting
    # them should still fail-fast so the rejection list guides it to a
    # valid MPERS label on its next turn.
    missing = [
        "Disclosure of capital management",
        "Disclosure of fair value measurement",
        "Disclosure of amendments to MFRS and pronouncements issued by MASB",
    ]
    for label in missing:
        assert _resolve_row(idx, label) is None, (
            f"{label!r} unexpectedly resolved on MPERS — the suffix "
            f"normaliser is over-matching."
        )
