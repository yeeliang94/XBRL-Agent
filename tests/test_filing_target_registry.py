"""Authoritative filing-target semantics across active template families."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from concept_model.notes_parser import parse_notes_template
from concept_model.parser import parse_template
from concept_model.filing_targets import audit_active_templates
from concept_model.taxonomy_semantics import taxonomy_concept
from notes.agent import _load_template_label_catalog
from notes.payload import NotesPayload
from notes.writer import write_notes_workbook


ROOT = Path(__file__).resolve().parent.parent


def test_taxonomy_abstract_is_not_a_reportable_primary_item():
    concept = taxonomy_concept("ssmt-mfrs_FinancialReportingStatusAbstract")

    assert concept is not None
    assert concept.abstract is True
    assert concept.concept_role == "ABSTRACT"
    assert concept.reportable is False
    assert concept.namespace_uri == (
        "http://xbrl.ssm.com.my/taxonomy/2022-12-31/ssmt-mfrs-cor"
    )
    assert concept.local_name == "FinancialReportingStatusAbstract"


def test_numeric_title_uses_taxonomy_semantics_not_header_fill():
    tree = parse_template(
        str(ROOT / "XBRL-template-MFRS/Company/01-SOFP-CuNonCu.xlsx")
    )
    title = next(
        node
        for node in tree.concepts
        if node.render_key["sheet"] == "SOFP-CuNonCu"
        and node.render_key["row"] == 3
    )

    assert title.canonical_label == "Statement of financial position"
    assert title.kind == "ABSTRACT"
    assert title.render_key["slot_role"] == "PRESENTATION_ONLY"
    assert title.render_key["semantic_address"]["primary_concept"] == (
        "ssmt_DisclosureOnStatementOfFinancialPositionAbstract"
    )


def test_financial_reporting_status_is_context_not_a_writable_note_target():
    template_id, nodes = parse_notes_template(
        str(ROOT / "XBRL-template-MFRS/Company/10-Notes-CorporateInfo.xlsx"),
        "Notes-CI",
    )

    assert template_id == "mfrs-company-notes-corporateinfo-v1"
    status = next(node for node in nodes if node.label == "Financial reporting status")
    explanation = next(
        node
        for node in nodes
        if node.label
        == "Explanation of reasons for the restatement of previous financial statements figures"
    )

    assert status.kind == "ABSTRACT"
    assert status.slot_role == "PRESENTATION_ONLY"
    assert status.taxonomy_element_id == "ssmt-mfrs_FinancialReportingStatusAbstract"
    assert explanation.kind == "LEAF"
    assert explanation.slot_role == "INPUT"
    assert explanation.taxonomy_element_id == (
        "ssmt-mfrs_ExplanationOfReasonsForRestatementOfPreviousFinancialStatementsFiguresExplanatory"
    )


def test_all_active_variants_have_complete_writable_semantics():
    audit = audit_active_templates(ROOT)

    assert audit["templates"] == 58
    assert audit["worksheets"] == 74
    assert audit["numeric_slots"] == 6356
    assert audit["prose_slots"] == 688
    assert audit["unclassified_slots"] == 0
    assert audit["missing_required_mappings"] == []
    assert audit["structural_writable_slots"] == []

    reviewed = Counter(
        item["code"] for item in audit["reviewed_exceptions"]
    )
    assert set(reviewed) == {
        "MFRS_ISSUED_CAPITAL_WRAPPER_OMITTED",
        "MFRS_RELATED_PARTY_WRAPPER_OMITTED",
        "PRESENTATION_TITLE_WITHOUT_TAXONOMY_SLOT",
        "SOCIE_SECTION_HEADER_WITHOUT_TAXONOMY_SLOT",
    }
    assert reviewed["MFRS_ISSUED_CAPITAL_WRAPPER_OMITTED"] == 2
    assert reviewed["MFRS_RELATED_PARTY_WRAPPER_OMITTED"] == 2

    numeric_notes = [
        item for item in audit["template_results"]
        if item["filename"] in {
            "13-Notes-IssuedCapital.xlsx",
            "14-Notes-RelatedParty.xlsx",
        }
    ]
    assert len(numeric_notes) == 4
    assert all(item["writable_slots"] > 0 for item in numeric_notes)
    assert all(item["mapped_writable_slots"] == item["writable_slots"] for item in numeric_notes)


def test_agent_catalog_and_writer_share_the_same_writable_targets(tmp_path):
    template = ROOT / "XBRL-template-MFRS/Company/10-Notes-CorporateInfo.xlsx"
    sheet = "Notes-CI"
    labels = _load_template_label_catalog(str(template), sheet)

    assert "Financial reporting status" not in labels
    valid = (
        "Explanation of reasons for the restatement of previous financial "
        "statements figures"
    )
    assert valid in labels

    rejected = write_notes_workbook(
        template_path=str(template),
        payloads=[NotesPayload(
            chosen_row_label="Financial reporting status",
            content="Presentation heading must not accept content.",
            evidence="Page 1",
            source_pages=[1],
            parent_note={"number": "1", "title": "Corporate information"},
        )],
        output_path=str(tmp_path / "rejected.xlsx"),
        filing_level="company",
        sheet_name=sheet,
    )
    assert rejected.success is False
    assert rejected.rows_written == 0
    assert any("No matching row" in error for error in rejected.errors)

    accepted = write_notes_workbook(
        template_path=str(template),
        payloads=[NotesPayload(
            chosen_row_label=valid,
            content="The prior-year figures were restated for the disclosed reason.",
            evidence="Page 2",
            source_pages=[2],
            parent_note={"number": "1", "title": "Corporate information"},
        )],
        output_path=str(tmp_path / "accepted.xlsx"),
        filing_level="company",
        sheet_name=sheet,
    )
    assert accepted.success is True
    assert accepted.rows_written == 1


def test_template_target_parsing_is_cached_per_file_revision(monkeypatch):
    import concept_model.filing_targets as filing_targets

    template = ROOT / "XBRL-template-MFRS/Company/10-Notes-CorporateInfo.xlsx"
    calls = 0
    original = filing_targets._prose_targets

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    filing_targets._targets_for_template_cached.cache_clear()
    monkeypatch.setattr(filing_targets, "_prose_targets", counted)
    filing_targets.writable_rows(template, "Notes-CI")
    filing_targets.writable_rows(template, "Notes-CI")

    assert calls == 1


def test_unchanged_manifest_skips_reparse_and_historical_sweeps(
    tmp_path, monkeypatch,
):
    import concept_model.filing_targets as filing_targets
    from db.schema import init_db

    db = tmp_path / "manifest.sqlite"
    template = ROOT / "XBRL-template-MFRS/Company/10-Notes-CorporateInfo.xlsx"
    init_db(db)
    count = filing_targets.persist_template_manifest(db, template)

    def unexpected_parse(_path):
        raise AssertionError("unchanged manifests must not be parsed again")

    monkeypatch.setattr(filing_targets, "targets_for_template", unexpected_parse)
    assert filing_targets.persist_template_manifest(db, template) == count
