"""Server wiring for source integrity — plan Steps 3.5 / 4.3 / 7.3.

The properties that matter at the seam, as opposed to inside the modules:

* the mode is PERSISTED on the run, not re-read from the environment later —
  a historical result has to stay explainable after the flag moves on;
* a source that cannot be read whole leaves the run on today's path with no
  verdict, rather than taking the run down or producing a short manifest;
* the verdict reaches run status through the ONE status block (gotcha #10),
  and only in `enforce`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import server as server_module
from db import repository as repo
from db.schema import init_db
from notes import integrity, source_manifest
from notes import source_repository as srepo
from notes.source_models import IntegrityMode

FIXTURE = Path("data/FINCO-Audited-Financial-Statement-2021.docx")


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "AUDIT_DB_PATH", tmp_path / "audit.sqlite")
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "x.docx", session_id="s", output_dir=str(tmp_path / "s")
        )
    return run_id, tmp_path


# --------------------------------------------------------------------------
# Step 3.5 — the mode is resolved once and persisted
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("off", IntegrityMode.OFF),
    ("shadow", IntegrityMode.SHADOW),
    ("enforce", IntegrityMode.ENFORCE),
    ("", IntegrityMode.OFF),
    ("nonsense", IntegrityMode.OFF),
])
def test_the_mode_resolves_from_the_environment(value, expected, monkeypatch):
    monkeypatch.setenv("XBRL_NOTES_SOURCE_INTEGRITY", value)
    assert server_module._notes_integrity_mode() is expected


def test_the_default_is_off(monkeypatch):
    """This feature ships dark. Anything else would change every run the
    moment it merges."""
    monkeypatch.delenv("XBRL_NOTES_SOURCE_INTEGRITY", raising=False)
    assert server_module._notes_integrity_mode() is IntegrityMode.OFF


def test_the_mode_is_recorded_on_the_run(wired):
    run_id, _tmp = wired
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        repo.set_notes_integrity_mode(conn, run_id, "shadow")
        assert repo.notes_integrity_mode(conn, run_id) == "shadow"


# --------------------------------------------------------------------------
# Step 4.3 — building the manifest
# --------------------------------------------------------------------------

def test_a_pdf_only_run_builds_no_manifest(wired):
    run_id, tmp = wired
    gen, report = server_module._build_source_manifest(
        run_id, tmp / "does-not-exist.docx"
    )
    assert gen is None and report is None


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_a_word_run_freezes_a_manifest_and_reports_boundaries(wired):
    run_id, _tmp = wired
    gen, report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=range(1, 16)
    )
    assert gen is not None
    assert report.ok is True
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        assert srepo.active_generation(conn, run_id)["id"] == gen
        assert len(srepo.fetch_blocks(conn, gen)) == 246


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_a_scout_disagreement_is_reported_not_resolved(wired):
    run_id, _tmp = wired
    _gen, report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=[1, 2, 3]
    )
    assert report.ok is False
    assert report.disagreements


def test_an_unreadable_source_raises_rather_than_shortening(wired, monkeypatch):
    """The run-level handler catches this and continues on the current path.
    What must never happen is a SHORT manifest that then reports complete."""
    run_id, tmp = wired
    broken = tmp / "broken.docx"
    broken.write_bytes(b"not a docx at all")
    with pytest.raises(source_manifest.ManifestError):
        server_module._build_source_manifest(run_id, broken)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        assert srepo.active_generation(conn, run_id) is None


# --------------------------------------------------------------------------
# Step 7.3 — reaching run status
# --------------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_an_uncovered_document_tips_the_run_in_enforce(wired):
    run_id, _tmp = wired
    gen, report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=range(1, 16)
    )
    outcome = server_module._run_notes_integrity_check(
        run_id, gen, IntegrityMode.ENFORCE, report
    )
    assert outcome["requires_review"] is True
    assert server_module._notes_integrity_tips_status(outcome) is True


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_shadow_records_the_same_verdict_and_does_not_tip(wired):
    run_id, _tmp = wired
    gen, report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=range(1, 16)
    )
    outcome = server_module._run_notes_integrity_check(
        run_id, gen, IntegrityMode.SHADOW, report
    )
    assert outcome["requires_review"] is True
    assert server_module._notes_integrity_tips_status(outcome) is False


def test_off_runs_no_check_at_all(wired):
    run_id, _tmp = wired
    assert server_module._run_notes_integrity_check(
        run_id, 1, IntegrityMode.OFF, None
    ) is None


def test_no_generation_runs_no_check(wired):
    run_id, _tmp = wired
    assert server_module._run_notes_integrity_check(
        run_id, None, IntegrityMode.ENFORCE, None
    ) is None


def test_a_missing_verdict_never_tips_the_run():
    """A check that did not run is not evidence of a problem."""
    assert server_module._notes_integrity_tips_status(None) is False
    assert server_module._notes_integrity_tips_status({}) is False


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_the_outcome_names_what_a_retry_should_fill(wired):
    run_id, _tmp = wired
    gen, report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=range(1, 16)
    )
    outcome = server_module._run_notes_integrity_check(
        run_id, gen, IntegrityMode.ENFORCE, report
    )
    assert outcome["missing_block_ids"]
    assert all(b.startswith("b") for b in outcome["missing_block_ids"])


@pytest.mark.skipif(not FIXTURE.is_file(), reason="Word fixture not present")
def test_a_boundary_disagreement_alone_requires_review(wired):
    """Step 4.4 / peer finding 3 — measuring it was not enough. A
    mis-assigned block otherwise reports 100% coverage and a wrong answer."""
    run_id, _tmp = wired
    gen, _report = server_module._build_source_manifest(
        run_id, FIXTURE, scout_note_nums=range(1, 16)
    )
    disputed = source_manifest.BoundaryReport(
        disagreements=[source_manifest.BoundaryDisagreement(
            "missing_trailing", "scout listed note 16", "16",
        )],
        scout_available=True, manifest_note_nums=[], scout_note_nums=[],
    )
    outcome = server_module._run_notes_integrity_check(
        run_id, gen, IntegrityMode.ENFORCE, disputed
    )
    assert any(f["check"] == "boundary" for f in outcome["findings"])
    assert outcome["requires_review"] is True


def test_the_status_block_is_the_only_status_writer():
    """Gotcha #10 allows exactly one writer. The integrity path returns a
    verdict; it must not update runs.status itself."""
    import inspect

    src = inspect.getsource(server_module._run_notes_integrity_check)
    assert "update_run_status" not in src
    assert "_safe_mark_finished" not in src
    assert "UPDATE runs" not in src


def test_the_reading_stage_is_a_known_pipeline_stage():
    """The stage label must exist in the frontend union or the UI renders a
    blank box (gotcha #19: both sides move together)."""
    types_ts = Path("web/src/lib/types.ts").read_text(encoding="utf-8")
    assert '"reading_source"' in types_ts
    page = Path("web/src/pages/ExtractPage.tsx").read_text(encoding="utf-8")
    assert 'reading_source' in page


def test_integrity_findings_are_json_serialisable(wired):
    """They are stored as JSON, so a dataclass leaking through would fail at
    write time on a real run rather than here."""
    import json

    result = integrity.IntegrityResult(findings=[
        integrity.Finding("disposition", integrity.UNRESOLVED, "m", ["b1"], "5")
    ])
    payload = [
        {"check": f.check, "severity": f.severity, "message": f.message,
         "block_ids": f.block_ids, "note_num": f.note_num}
        for f in result.findings
    ]
    assert json.loads(json.dumps(payload))[0]["check"] == "disposition"
