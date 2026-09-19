import json

import pytest

from notes.source_manifest import ManifestError, build_prepared_manifest


def prepared(tmp_path, **changes):
    data = {"status": "succeeded", "inventory_reconciled": True, "source_sha256": "abc", "revision": "v1", "page_count": 1,
            "pages": [{"page": 1, "verified": True}], "blocks": [
                {"block_id": "h", "block_kind": "heading", "reading_order": 0,
                 "canonical_html": "<h3>3. Policy</h3>", "page": 1,
                 "owner_kind": "note", "source_note_id": "n3", "source_note_num": "3"},
                {"block_id": "p", "block_kind": "paragraph", "reading_order": 1,
                 "canonical_html": "<p>Exact text.</p>", "page": 1,
                 "owner_kind": "note", "source_note_id": "n3", "source_note_num": "3",
                 "locator": {"heading_ancestor_ids": ["h"]}},
            ]}
    data.update(changes)
    path = tmp_path / "preparation.json"
    path.write_text(json.dumps(data))
    return path


def test_prepared_manifest_keeps_structure_and_verified_page_receipts(tmp_path):
    manifest, report = build_prepared_manifest(prepared(tmp_path), scout_note_nums=[3])
    assert report.ok
    assert manifest.input_kind == "prepared_document"
    assert manifest.pages_expected == manifest.pages_processed == 1
    assert manifest.blocks[1].locator["heading_ancestor_ids"] == ["h"]


def test_missing_page_receipt_cannot_shrink_original_document_denominator(tmp_path):
    import fitz
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page()
        doc.save(tmp_path / "uploaded.pdf")
    with pytest.raises(ManifestError, match="every original page"):
        build_prepared_manifest(prepared(tmp_path), scout_note_nums=[3])


def test_unverified_page_cannot_become_source_denominator(tmp_path):
    with pytest.raises(ManifestError, match="independently verified"):
        build_prepared_manifest(prepared(tmp_path, pages=[{"verified": False}]), scout_note_nums=[3])


def test_inventory_disagreement_requires_source_repair(tmp_path):
    with pytest.raises(ManifestError, match="disagree"):
        build_prepared_manifest(prepared(tmp_path), scout_note_nums=[3, 4])


def test_unresolved_ownership_cannot_be_silently_excluded(tmp_path):
    path = prepared(tmp_path)
    data = json.loads(path.read_text())
    data["blocks"][1]["owner_kind"] = "unresolved"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="ownership"):
        build_prepared_manifest(path, scout_note_nums=[3])


def test_best_effort_capture_proceeds_with_nonblocking_uncertainty_provenance(tmp_path):
    from db import repository as repo
    from db.schema import init_db
    from notes import integrity, integrity_runner, lineage, source_write
    from notes.source_manifest import freeze_manifest

    path = prepared(tmp_path, pages=[{"page": 1, "verified": False,
        "assessment_complete": True, "capture_status": "best_effort",
        "uncertainties": [{"reason": "Faint wording reconstructed from context",
                           "observed_text": "Exact t...", "reconstructed_text": "Exact text."}]}])
    manifest, _ = build_prepared_manifest(path, scout_note_nums=[3])
    assert manifest.blocks[1].locator["capture_uncertain"]
    assert manifest.blocks[1].locator["uncertainties"][0]["reconstructed_text"] == "Exact text."
    db = tmp_path / "audit.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = freeze_manifest(conn, run, manifest)
        source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
            sheet="Notes", row=10, block_ids=["p"])
        state = lineage.read_lineage(conn, run, "Notes", 10)
        assert state.content_origin == "vision_transcribed"
        result = integrity.run_checks(integrity_runner.build_input(conn, run, gen, scout_available=True))
        assert not result.requires_review
        assert any(f.check == "source_uncertainty" and not f.blocking for f in result.findings)
        assert integrity.missing_block_ids(result) == []
        html = conn.execute("SELECT html FROM notes_cells WHERE run_id=?", (run,)).fetchone()[0]
        lineage.mark_human_edit(conn, run, "Notes", 10, "<p>Human replacement</p>")
        restored = lineage.mark_human_edit(conn, run, "Notes", 10, html)
        assert restored.content_origin == "vision_transcribed"
        assert not restored.diverged


def test_unreadable_page_without_explicit_assessment_still_is_not_processed(tmp_path):
    path = prepared(tmp_path, pages=[{"page": 1, "verified": False, "capture_status": "best_effort"}])
    with pytest.raises(ManifestError, match="assessment"):
        build_prepared_manifest(path, scout_note_nums=[3])


def test_administrative_stamp_can_be_accounted_as_document_metadata(tmp_path):
    path = prepared(tmp_path)
    data = json.loads(path.read_text())
    data["blocks"].append({"block_id": "stamp", "block_kind": "paragraph", "reading_order": 2,
        "canonical_html": "<p>RECEIVED</p>", "page": 1, "owner_kind": "metadata",
        "locator": {"reason": "DOCUMENT_METADATA", "region_kind": "administrative_stamp",
                    "bbox": [1, 2, 30, 40]}})
    path.write_text(json.dumps(data))
    manifest, _ = build_prepared_manifest(path, scout_note_nums=[3])
    assert len(manifest.blocks) == 3
    assert manifest.notes[0].block_ids == ["h", "p"]
    assert manifest.blocks[-1].locator["region_kind"] == "administrative_stamp"
