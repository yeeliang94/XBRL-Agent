"""Phase 3 MPERS hardening — integration test proving the factory
seed + Phase 2 suffix normalisation compose to fix the run-#105 drop.

Red-green-refactor anchor for `docs/Archive/PLAN-mpers-notes-hardening.md`
Phase 3 Step 3.3. The test simulates the end-to-end path an MPERS
sub-agent takes:
  1. Factory loads the MPERS LIST_OF_NOTES label catalog.
  2. Agent emits a payload with a bare label (MFRS-style wording).
  3. Writer's label resolver matches it to the MPERS [text block] row.

Before Phase 2+3 this path silently rejected the payload. The
assertion below is the regression lock.
"""
from __future__ import annotations

from concept_model.filing_targets import writable_rows
from notes.agent import (
    NotesDeps,
    _render_notes_template_hierarchy,
    create_notes_agent,
)
from notes.writer import _resolve_row, _build_label_index
from notes_types import NotesTemplateType, notes_template_path
from tools.template_reader import read_template


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


def test_issued_capital_catalog_keeps_unit_bearing_section_paths() -> None:
    """Repeated balance labels must retain the template headers that say
    whether the row is a monetary amount or a number of shares."""
    template_path = notes_template_path(
        NotesTemplateType.ISSUED_CAPITAL,
        level="company",
        standard="mfrs",
    )
    sheet = "Notes-Issuedcapital"
    allowed = writable_rows(str(template_path), sheet)

    rendered = _render_notes_template_hierarchy(
        read_template(str(template_path)),
        sheet,
        set(allowed) if allowed is not None else None,
    )

    assert "Common section path: Notes - Issued capital" in rendered
    assert (
        "row  11: Shares issued and fully paid > "
        "Amount of shares issued and fully paid > Balance at the beginning "
        "of period"
    ) in rendered
    assert (
        "row  30: Shares outstanding > "
        "Number of shares outstanding > *Number of shares outstanding at "
        "end of period"
    ) in rendered


def test_flat_catalog_emits_the_constant_root_once() -> None:
    template_path = notes_template_path(
        NotesTemplateType.LIST_OF_NOTES,
        level="company",
        standard="mfrs",
    )
    sheet = "Notes-Listofnotes"
    allowed = writable_rows(str(template_path), sheet)

    rendered = _render_notes_template_hierarchy(
        read_template(str(template_path)),
        sheet,
        set(allowed) if allowed is not None else None,
    )

    root = "Notes - List of notes"
    assert rendered.count(root) == 1
    assert f"Common section path: {root}" in rendered
    assert f"{root} >" not in rendered
