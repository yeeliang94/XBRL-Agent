from db import repository as repo
from db.schema import init_db
from notes import source_repository as srepo, source_write
from notes.source_models import SourceBlock
from notes.source_repair import repair_recorded_placements


def test_repair_does_not_move_policy_content_into_other_note_destination(tmp_path):
    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [
            SourceBlock("policy", "paragraph", 1, "<p>Policy.</p>", source_note_id="n2"),
            SourceBlock("detail", "paragraph", 2, "<p>Details.</p>", source_note_id="n2"),
            SourceBlock("tail", "paragraph", 3, "<p>More details.</p>", source_note_id="n2"),
        ])
        srepo.activate_generation(conn, gen)
        def write(sheet, row, ids):
            source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
                sheet=sheet, row=row, block_ids=ids)
        write("Policies", 3, ["policy"])
        write("Notes", 4, ["detail", "tail"])
        write("Notes", 4, ["detail"])
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=["tail"]) == 1
        rows = conn.execute("SELECT sheet,html FROM notes_cells WHERE run_id=?", (run,)).fetchall()
        cells = {r["sheet"]: r["html"] for r in rows}
        assert "Policy." in cells["Policies"] and "details" not in cells["Policies"]
        assert "More details." in cells["Notes"] and "Policy." not in cells["Notes"]


def test_corrupted_source_cell_repairs_from_existing_placements(tmp_path):
    from notes.integrity import run_checks, missing_block_ids
    from notes.integrity_runner import build_input

    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("a", "paragraph", 1, "<p>Complete text.</p>")])
        srepo.activate_generation(conn, gen)
        source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
            sheet="Notes", row=3, block_ids=["a"])
        conn.execute("UPDATE notes_cells SET html='<p>Short</p>' WHERE run_id=?", (run,))
        conn.commit()
        before = run_checks(build_input(conn, run, gen))
        ids = missing_block_ids(before)
        assert "a" in ids
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=ids) == 1
        html = conn.execute("SELECT html FROM notes_cells WHERE run_id=?", (run,)).fetchone()[0]
        assert html == "<p>Complete text.</p>"


def test_corrupted_human_cell_is_never_automatically_repaired(tmp_path):
    from notes import lineage
    db = tmp_path / "db.sqlite"
    init_db(db)
    with repo.db_session(db) as conn:
        run = repo.create_run(conn, "source.pdf", session_id="s", output_dir=str(tmp_path))
        gen = srepo.begin_generation(conn, run, input_kind="prepared_document")
        srepo.write_blocks(conn, gen, [SourceBlock("a", "paragraph", 1, "<p>Source.</p>")])
        srepo.activate_generation(conn, gen)
        source_write.write_cell_from_blocks(conn, run_id=run, generation_id=gen,
            sheet="Notes", row=3, block_ids=["a"])
        lineage.mark_human_edit(conn, run, "Notes", 3, "<p>Human.</p>")
        assert repair_recorded_placements(conn, run_id=run, generation_id=gen,
                                         missing_block_ids=["a"]) == 0
