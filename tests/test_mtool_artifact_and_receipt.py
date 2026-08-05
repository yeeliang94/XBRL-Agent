"""Steps 11A + 19 — see the report before you hold the file, and leave a record.

**11A.** The old endpoint streamed the workbook AND squeezed the report into an
HTTP header, capped at 20 rows / 6 KB. The download fired first, so the
operator held the file before they could see whether it was clean — the exact
thing Step 11's own acceptance criterion asked for. Now: patch returns the full
report plus an artifact id; a second request fetches the file, and a degraded
fill won't release it without an explicit acknowledgement.

**19.** A filled MBRS workbook is a regulatory artifact, and a completed run
stays editable, so two fills of "the same" run can legitimately differ. Every
fill writes one receipt recording the fact revision, both file hashes, the
template fingerprint, the column map, the preflight verdict and any override.
"""
from __future__ import annotations

import importlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
SOFP = REPO / "XBRL-template-MFRS" / "Company" / "01-SOFP-CuNonCu.xlsx"


def _upload():
    return {"template": ("t.xlsx", io.BytesIO(SOFP.read_bytes()),
                         "application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet")}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRL_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-12345")
    monkeypatch.setenv("LLM_PROXY_URL", "")
    import server as srv
    importlib.reload(srv)
    db = tmp_path / "xbrl.db"
    srv.AUDIT_DB_PATH = db
    from db.schema import init_db
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    init_db(db)
    tree = parse_template(str(SOFP))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_company_targets(db, import_template(db, jp))
    return TestClient(srv.app), db, srv


def _make_run(db) -> int:
    conn = sqlite3.connect(str(db))
    try:
        run_id = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at, "
            "run_config_json) VALUES (?,?,?,?,?)",
            ("2026-07-27T00:00:00Z", "x.pdf", "completed",
             "2026-07-27T00:00:00Z",
             json.dumps({"filing_standard": "mfrs", "filing_level": "company",
                         "denomination": "thousands"}))).lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed(db, run_id, n=4, base=1000):
    conn = sqlite3.connect(str(db))
    try:
        leaves = conn.execute(
            """
            SELECT concept_uuid FROM concept_nodes
            WHERE kind='LEAF' AND render_sheet LIKE '%Sub%'
              AND canonical_label IN (
                SELECT canonical_label FROM concept_nodes
                GROUP BY canonical_label HAVING COUNT(*) = 1)
            ORDER BY render_row LIMIT ?
            """, (n,)).fetchall()
        for i, (uuid,) in enumerate(leaves):
            conn.execute(
                "INSERT OR REPLACE INTO run_concept_facts("
                "run_id, concept_uuid, period, entity_scope, value, "
                "value_status, updated_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, uuid, "CY", "Company", base + i, "observed", "t"))
        conn.commit()
    finally:
        conn.close()
    return len(leaves)


def _patch(tc, run_id, **data):
    return tc.post(f"/api/runs/{run_id}/mtool-fill/patch", files=_upload(),
                   data={"strict": "true", **data})


# --------------------------------------------------------------- Step 11A

def test_patch_returns_a_report_not_a_file(client):
    tc, db, _ = client
    run_id = _make_run(db)
    n = _seed(db, run_id)
    resp = _patch(tc, run_id)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["counts"]["written"] == n
    assert body["artifact_id"]
    assert body["download_url"].endswith(body["artifact_id"])
    assert body["artifact_expires_in_s"] > 0


def test_clean_fill_downloads_without_ceremony(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _patch(tc, run_id).json()
    resp = tc.get(body["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats")
    import openpyxl
    openpyxl.load_workbook(io.BytesIO(resp.content))


def _degraded(tc, db, run_id):
    """Force a degraded fill: point the label column at an empty column so
    every write goes unresolved."""
    doc = tc.get(f"/api/runs/{run_id}/mtool-fill").json()
    sheet = doc["meta"]["sheets_covered"][0]
    cmap = {sheet: {"label_column": "Z", "columns": {"current_year": "B"}}}
    return _patch(tc, run_id, column_map=json.dumps(cmap))


def test_degraded_fill_withholds_the_file_until_acknowledged(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _degraded(tc, db, run_id).json()
    assert body["status"] == "degraded"
    assert body["counts"]["unresolved"] > 0

    blocked = tc.get(body["download_url"])
    assert blocked.status_code == 409
    assert "Read the report" in blocked.json()["detail"]

    ok = tc.get(body["download_url"],
                params={"acknowledge_degraded": "I read it, proceed"})
    assert ok.status_code == 200


def test_degraded_acknowledgement_lands_on_the_receipt(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    body = _degraded(tc, db, run_id).json()
    tc.get(body["download_url"],
           params={"acknowledge_degraded": "aware of 4 unplaced rows"})
    receipt = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"][0]
    assert receipt["degraded_ack"] == "aware of 4 unplaced rows"
    assert receipt["downloaded_at"]


def test_artifact_is_scoped_to_its_run(client):
    tc, db, _ = client
    run_a, run_b = _make_run(db), _make_run(db)
    _seed(db, run_a)
    _seed(db, run_b)
    artifact = _patch(tc, run_a).json()["artifact_id"]
    resp = tc.get(f"/api/runs/{run_b}/mtool-fill/artifact/{artifact}")
    assert resp.status_code == 404


def test_unknown_artifact_404s(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    assert tc.get(
        f"/api/runs/{run_id}/mtool-fill/artifact/nope").status_code == 404


def test_artifacts_are_capped_and_oldest_evicted(client, tmp_path,
                                                 monkeypatch):
    tc, db, _ = client
    import api.mtool as m
    monkeypatch.setattr(m, "_MAX_LIVE_ARTIFACTS", 2)
    run_id = _make_run(db)
    _seed(db, run_id)
    first = _patch(tc, run_id).json()
    _patch(tc, run_id)
    _patch(tc, run_id)
    # The oldest was evicted rather than accumulating on disk forever.
    assert tc.get(first["download_url"]).status_code == 404
    assert len(list((tmp_path / "_mtool_tmp").glob("*"))) <= 2


# ----------------------------------------------------------------- Step 19

def test_a_fill_writes_exactly_one_receipt(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _patch(tc, run_id)
    receipts = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"]
    assert len(receipts) == 1
    r = receipts[0]
    assert r["status"] == "ok"
    assert len(r["source_sha256"]) == 64
    assert len(r["output_sha256"]) == 64
    assert r["source_sha256"] != r["output_sha256"]  # we did write something
    assert r["template_fingerprint"]
    assert r["translation_version"] == "identity-1"
    assert r["column_map"]
    assert r["snapshot"]["fact_count"] == 4
    assert r["report"]["counts"]["written"] == 4
    assert r["created_at"]
    assert r["downloaded_at"] is None  # not taken yet


def test_refilling_the_same_run_yields_two_distinguishable_receipts(client):
    """The point of the receipt: a completed run stays editable, so 'the same
    run' can produce two different workbooks. Both are recorded, with distinct
    output hashes and distinct fact-revision digests."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id, base=1000)
    _patch(tc, run_id)
    _seed(db, run_id, base=9000)   # someone corrects the figures
    _patch(tc, run_id)

    receipts = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"]
    assert len(receipts) == 2
    newer, older = receipts  # newest first
    assert newer["output_sha256"] != older["output_sha256"]
    assert newer["snapshot"]["digest"] != older["snapshot"]["digest"]


def test_receipt_records_who_filled_when_a_session_exists(client):
    """Best-effort: in dev mode there is no resolvable session, and an
    anonymous receipt is far better than a failed fill."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _patch(tc, run_id)
    receipt = tc.get(
        f"/api/runs/{run_id}/mtool-fill/receipts").json()["receipts"][0]
    assert "operator" in receipt


def test_a_failed_receipt_write_withholds_the_artifact(client, tmp_path,
                                                       monkeypatch):
    """No receipt, no artifact (peer review, 2026-08-05).

    The original posture ("the receipt is best-effort, never fail a good
    fill") meant a registrar-bound workbook could exist with no record of
    what produced it — the exact gap the receipts table exists to close. Now
    a failed receipt write fails the request with a plain 500, registers NO
    artifact, and cleans the temp dir.
    """
    tc, db, _ = client
    import api.mtool as m
    monkeypatch.setattr(m, "write_fill_receipt",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("audit db is down")))
    run_id = _make_run(db)
    _seed(db, run_id)
    before = set(m._ARTIFACTS)
    resp = _patch(tc, run_id)
    assert resp.status_code == 500, resp.text
    assert "receipt" in str(resp.json()["detail"])
    assert "was not released" in str(resp.json()["detail"])
    assert set(m._ARTIFACTS) == before, \
        "no artifact may be registered without a receipt"
    leftovers = list((tmp_path / "_mtool_tmp").glob("*"))
    assert leftovers == [], f"temp dir leaked on a late failure: {leftovers}"


def test_receipt_module_raises_on_write_failure(tmp_path):
    """The module-level half of the same contract: the write RAISES rather
    than degrading to None, so no caller can quietly ignore a lost receipt."""
    from mtool.receipt import write_fill_receipt
    missing = tmp_path / "no-such-dir" / "x.db"
    with pytest.raises(Exception):
        write_fill_receipt(
            missing, run_id=1, snapshot={}, source_sha256=None,
            output_sha256=None, template_fingerprint=None, column_map=None,
            translation_version=None, preflight=None, preflight_override=None,
            operator=None, report=None)


def test_snapshot_is_one_consistent_read(tmp_path):
    """The helper returns the NUMERIC rows and the identity of that revision.

    It speaks for `run_concept_facts` only — the prose has its own identity
    (see the notes-snapshot tests below), because it comes from a separate
    read of `notes_cells`."""
    from concept_model.importer import import_company_targets, import_template
    from concept_model.parser import parse_template
    from db.schema import init_db
    from mtool.receipt import snapshot_facts

    db = tmp_path / "x.db"
    init_db(db)
    tree = parse_template(str(SOFP))
    jp = tmp_path / "t.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    import_company_targets(db, import_template(db, jp))
    run_id = _make_run(db)
    _seed(db, run_id, n=3)

    rows, identity = snapshot_facts(db, run_id, filing_standard="mfrs",
                                    filing_level="company")
    assert len(rows) == 3
    assert identity["fact_count"] == 3
    assert identity["max_updated_at"] == "t"
    # Re-reading unchanged data gives the same identity.
    assert snapshot_facts(db, run_id, filing_standard="mfrs",
                          filing_level="company")[1] == identity


# ------------------------------------------------- the PROSE revision (v39)
#
# v38's receipt identified only the numeric fact revision. The prose is read
# from `notes_cells` by its own connection, later in the same request, so a
# notes edit landing between the two reads produced a workbook whose prose no
# receipt described. Both revisions are now recorded.


def _seed_notes(db, run_id, html="<p>Note one</p>", label="Corporate information"):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, "Notes-CI", 5, label, html, "2026-08-05T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def _written(indices):
    """A fill report shaped like one where `indices` were actually written.

    The SOFP fixture carries no `fn_*` slots, so a real fill through it always
    resolves ZERO notes — which is exactly the case that must record no prose
    revision. To exercise the WRITTEN path these tests stand in a report that
    resolved the notes, rather than pretending the fixture did."""
    def _fake(src, doc, out, **kwargs):
        import shutil
        if out:
            shutil.copyfile(src, out)
        return {
            "status": "ok",
            "footnotes_written": [
                {"index": i, "key": f"fn_{i}",
                 "label": doc["footnotes"][i]["label"]}
                for i in indices],
            "footnotes_created": [], "unresolved": [],
            "footnote_mismatches": [], "errors": [],
        }
    return _fake


def _receipt(db, run_id):
    from mtool.receipt import fetch_receipts
    return fetch_receipts(db, run_id)[0]


def test_receipt_records_the_prose_revision_alongside_the_numeric_one(
        client, monkeypatch):
    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id)

    monkeypatch.setattr(m, "fill_footnotes", _written([0]))
    assert _patch(tc, run_id).status_code == 200
    rec = _receipt(db, run_id)
    assert rec["snapshot"]["digest"], "numeric revision still recorded"
    assert rec["notes_snapshot"]["digest"], "prose revision recorded too"
    assert rec["notes_snapshot"]["notes_count"] == 1
    assert rec["notes_snapshot"]["max_updated_at"] == "2026-08-05T00:00:00Z"


def test_editing_a_note_moves_the_prose_digest_with_the_numbers_unchanged(
        client, monkeypatch):
    """The failure this closes: identical facts, edited prose, and — before
    v39 — two receipts that were indistinguishable."""
    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id)
    monkeypatch.setattr(m, "fill_footnotes", _written([0]))
    assert _patch(tc, run_id).status_code == 200
    first = _receipt(db, run_id)

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE notes_cells SET html = ? WHERE run_id = ?",
            ("<p>Note one, corrected</p>", run_id))
        conn.commit()
    finally:
        conn.close()

    assert _patch(tc, run_id).status_code == 200
    second = _receipt(db, run_id)
    assert second["snapshot"]["digest"] == first["snapshot"]["digest"]
    assert second["notes_snapshot"]["digest"] != first["notes_snapshot"]["digest"]


def test_a_numeric_only_fill_records_no_prose_revision(client):
    """`None` means "this fill wrote no prose" — a different fact from "the
    prose was empty", so it must not be a digest over nothing."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id)

    assert _patch(tc, run_id, fill_notes="false").status_code == 200
    assert _receipt(db, run_id)["notes_snapshot"]["digest"] is None


def test_prose_digest_ignores_the_styling_knobs(client, monkeypatch):
    """The digest identifies the DATA revision, not the rendering.

    Styling is chosen per request (`notes_styling`) and the decorator rewrites
    the HTML on the way out. If the digest tracked the rendered form, two fills
    of identical prose would look like an edit — and a real edit made between
    them would be indistinguishable from a styling change."""
    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id, html="<table><tr><td>1</td></tr></table>")

    monkeypatch.setattr(m, "fill_footnotes", _written([0]))
    assert _patch(tc, run_id, notes_styling="styled").status_code == 200
    styled = _receipt(db, run_id)
    assert _patch(tc, run_id, notes_styling="none").status_code == 200
    plain = _receipt(db, run_id)
    assert plain["notes_snapshot"]["digest"] == styled["notes_snapshot"]["digest"]
    # And the two modes really did render the note differently, so the
    # equality above is a property of the digest and not of the input.
    from mtool.notes_exporter import build_notes_fill_doc
    a = build_notes_fill_doc(db, run_id, decorate=True)["footnotes"][0]["html"]
    b = build_notes_fill_doc(db, run_id, decorate=False)["footnotes"][0]["html"]
    assert a != b


# ------------------------------------------------- operator free text bounds


def test_an_overlong_override_is_clamped_not_stored_whole(client):
    from mtool.receipt import ACK_TEXT_LIMIT

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    # Force a blocking preflight so the override is required and recorded.
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
            "created_at) VALUES (?,?,?,?,?)",
            (run_id, "stuck", "unresolved", "open", "t"))
        conn.commit()
    finally:
        conn.close()

    assert _patch(tc, run_id, acknowledge_preflight="y" * 50_000).status_code == 200
    stored = _receipt(db, run_id)["preflight_override"]
    assert len(stored) <= ACK_TEXT_LIMIT
    assert stored.endswith("[truncated]")


def test_a_normal_length_override_is_stored_verbatim(client):
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO reviewer_flags(run_id, category, reasoning, status, "
            "created_at) VALUES (?,?,?,?,?)",
            (run_id, "stuck", "unresolved", "open", "t"))
        conn.commit()
    finally:
        conn.close()

    reason = "Partner reviewed the imbalance on 5 Aug and approved filing."
    assert _patch(tc, run_id, acknowledge_preflight=reason).status_code == 200
    assert _receipt(db, run_id)["preflight_override"] == reason


# ------------------------------------------- gates that apply to BOTH paths


def test_auto_detected_map_is_semantically_validated_too(client, monkeypatch):
    """An operator-supplied map is checked for collisions and label overwrites;
    the auto-detected one is the DEFAULT and was not. The detector cannot
    currently produce such a map, which is exactly why the check has to be
    here — otherwise the invariant rests on the detector's internals and a
    future change to them breaks a filing rather than a request."""
    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)

    real = m.detect_column_map

    def onto_the_labels(*args, **kwargs):
        detected = real(*args, **kwargs)
        for layout in detected.values():
            # Confident, unattended-eligible, and wrong: the figures are
            # addressed to the very column holding the labels they are matched
            # by, so the fill would erase its own row headings.
            layout["confidence"] = "high"
            layout["requires_confirmation"] = False
            layout["columns"] = {
                r: layout["label_column"] for r in layout["columns"]}
        return detected

    monkeypatch.setattr(m, "detect_column_map", onto_the_labels)
    resp = _patch(tc, run_id)
    assert resp.status_code == 422, resp.text
    assert "overwrite the row labels" in json.dumps(resp.json())


def test_concurrent_registration_during_a_sweep_does_not_explode(client):
    """`_ARTIFACTS` is touched from the event loop (patch) and from a worker
    thread (download). An unsynchronised sweep iterating it while another
    request registers raises RuntimeError and 500s a fill that had already
    succeeded."""
    import threading

    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)

    errors: list[BaseException] = []
    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            try:
                m._register_artifact(
                    run_id, Path("/nonexistent"), Path("/nonexistent/f.xlsx"),
                    status="ok", receipt_id=None)
                m._sweep_artifacts()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                return
            i += 1
            if i > 400:
                return

    threads = [threading.Thread(target=churn) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    stop.set()
    assert not errors, f"registry raced: {errors[0]!r}"


def test_a_degraded_notes_fill_records_no_prose_revision(client, monkeypatch):
    """When the notes fill fails, the operator gets the numeric-only workbook.
    A receipt naming a prose revision that is not in that file would be the
    same untraceability the snapshot exists to close."""
    import api.mtool as m

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id)

    def boom(*args, **kwargs):
        raise RuntimeError("notes patcher fell over")

    monkeypatch.setattr(m, "fill_footnotes", boom)
    body = _patch(tc, run_id).json()
    assert body["notes"]["status"] == "degraded"
    assert _receipt(db, run_id)["notes_snapshot"]["digest"] is None


def test_a_fill_that_wrote_no_notes_records_no_prose_revision(client):
    """The reproduced bug: the SOFP template exposes no `fn_*` slots, so
    fill_footnotes resolves nothing and returns a normal degraded report — yet
    the receipt claimed a prose revision. Producing a notes workbook is not the
    same as filling anything into it."""
    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id)

    body = _patch(tc, run_id).json()
    assert body["notes"]["counts"]["written"] == 0, "fixture has no fn_ slots"
    assert _receipt(db, run_id)["notes_snapshot"]["digest"] is None


def test_a_partial_notes_fill_attests_only_to_what_landed(client, monkeypatch):
    """Two notes offered, one written. The digest must cover the written one
    alone — attesting to the unresolved note would be the same false claim as
    the zero-write case, just harder to spot."""
    import api.mtool as m
    from mtool.notes_exporter import build_notes_fill_doc, build_notes_snapshot

    tc, db, _ = client
    run_id = _make_run(db)
    _seed(db, run_id)
    _seed_notes(db, run_id, html="<p>First</p>", label="Corporate information")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO notes_cells(run_id, sheet, row, label, html, "
            "updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, "Notes-CI", 9, "Significant accounting policies",
             "<p>Second</p>", "2026-08-06T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(m, "fill_footnotes", _written([0]))
    assert _patch(tc, run_id).status_code == 200
    partial = _receipt(db, run_id)["notes_snapshot"]
    assert partial["notes_count"] == 1
    # It is note 0 that landed: the digest equals the one-note snapshot, and
    # NOT the both-notes snapshot.
    doc = build_notes_fill_doc(db, run_id)
    assert partial["digest"] == build_notes_snapshot(doc, [0])["digest"]
    assert partial["digest"] != build_notes_snapshot(doc, [0, 1])["digest"]
    # …and the note that did not land is not implied by the timestamp either.
    assert partial["max_updated_at"] == "2026-08-05T00:00:00Z"


def test_build_notes_snapshot_ignores_indices_it_cannot_place(client):
    """A written entry with a missing or out-of-range index must not silently
    shift the digest onto some other note."""
    from mtool.notes_exporter import build_notes_fill_doc, build_notes_snapshot

    tc, db, _ = client
    run_id = _make_run(db)
    _seed_notes(db, run_id)
    doc = build_notes_fill_doc(db, run_id)
    assert build_notes_snapshot(doc, [None, 7, -1]) is None
    assert build_notes_snapshot(doc, []) is None
    assert build_notes_snapshot(doc, [0, 0])["notes_count"] == 1
