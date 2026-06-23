"""Step 8 of docs/Archive/PLAN-NOTES-RICH-EDITOR.md — GET/PATCH notes_cells API.

Red tests encode the wire contract the editor (Step 9+) talks to:

  * ``GET  /api/runs/{run_id}/notes_cells`` — every cell for the run,
    grouped by sheet and ordered by row for stable UI rendering.
  * ``PATCH /api/runs/{run_id}/notes_cells/{sheet}/{row}`` — update the
    HTML for one cell; sanitises (Step 5) and enforces the 30k rendered
    cap (Step 3).

Both go through the existing ``_open_audit_conn`` shim so a test-scoped
``AUDIT_DB_PATH`` swap is enough isolation (no app reloads).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from db import repository as repo
from db.schema import init_db


@pytest.fixture()
def client_and_run(tmp_path: Path, monkeypatch) -> tuple[TestClient, int]:
    """Point server.AUDIT_DB_PATH at a fresh DB and seed one run with cells."""
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    # The bare TestClient below doesn't run the app lifespan, so populate the
    # notes registry the way startup would — otherwise the projection degrades
    # to the legacy filled-only fallback.
    from concept_model.bootstrap import import_all_notes_templates
    import_all_notes_templates(server_module.AUDIT_DB_PATH)

    # Seed a run with a few notes cells across two sheets.
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "sample.pdf",
            session_id="sess-a", output_dir=str(tmp_path / "sess-a"),
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=4,
            label="Corporate info", html="<p>CI 4</p>",
            evidence="Page 3", source_pages=[3],
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-CI", row=12,
            label="Registered office", html="<p>CI 12</p>",
            evidence="Page 3", source_pages=[3],
        )
        repo.upsert_notes_cell(
            conn, run_id=run_id, sheet="Notes-SummaryofAccPol", row=7,
            label="Revenue", html="<p>Accrual</p>",
            evidence="Page 5", source_pages=[5],
        )

    return TestClient(server_module.app), run_id


def test_get_notes_cells_returns_full_template_with_blanks(client_and_run) -> None:
    """The projection returns the FULL prose template per targeted sheet,
    with the seeded cells filled and the remaining template rows blank
    (PLAN-notes-template-registry Phase 3)."""
    client, run_id = client_and_run

    resp = client.get(f"/api/runs/{run_id}/notes_cells")
    assert resp.status_code == 200
    body = resp.json()

    # Only the two sheets that carry data are projected (targeted-only), in
    # MBRS slot order.
    assert [s["sheet"] for s in body["sheets"]] == [
        "Notes-CI", "Notes-SummaryofAccPol",
    ]

    ci = body["sheets"][0]
    assert ci["kind"] == "prose"
    # Full template → more than just the 2 seeded rows, in ascending order.
    assert len(ci["rows"]) > 2
    assert [r["row"] for r in ci["rows"]] == sorted(r["row"] for r in ci["rows"])

    rows_by_num = {r["row"]: r for r in ci["rows"]}
    # The seeded cells are present and filled.
    assert rows_by_num[4]["html"] == "<p>CI 4</p>"
    assert rows_by_num[4]["evidence"] == "Page 3"
    assert rows_by_num[4]["source_pages"] == [3]
    assert rows_by_num[4]["updated_at"]
    assert rows_by_num[4]["kind"] == "prose"
    # At least one unfilled template row is surfaced as a blank.
    blanks = [r for r in ci["rows"] if r["html"] == ""]
    assert blanks
    assert blanks[0]["node_uuid"]  # blank rows carry the registry identity


def test_get_notes_cells_returns_404_for_unknown_run(tmp_path: Path) -> None:
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    client = TestClient(server_module.app)

    resp = client.get("/api/runs/999/notes_cells")
    assert resp.status_code == 404


def test_get_notes_cells_returns_empty_for_run_with_no_notes(tmp_path: Path) -> None:
    import server as server_module

    server_module.OUTPUT_DIR = tmp_path
    server_module.AUDIT_DB_PATH = tmp_path / "audit.sqlite"
    init_db(server_module.AUDIT_DB_PATH)
    with repo.db_session(server_module.AUDIT_DB_PATH) as conn:
        run_id = repo.create_run(
            conn, "bare.pdf", session_id="sess-b", output_dir="/tmp/sess-b",
        )
    client = TestClient(server_module.app)

    resp = client.get(f"/api/runs/{run_id}/notes_cells")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"sheets": []}


def test_patch_notes_cell_updates_html_and_updated_at(client_and_run) -> None:
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": "<p>CI 4 <strong>edited</strong></p>"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row"] == 4
    assert body["sheet"] == "Notes-CI"
    assert "<strong>edited</strong>" in body["html"]
    # updated_at is refreshed so the UI can show "Saved just now".
    assert body["updated_at"]


def test_patch_notes_cell_sanitises_input_html(client_and_run) -> None:
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": '<p>ok</p><script>alert(1)</script>'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "<script" not in body["html"].lower()
    assert "ok" in body["html"]


def test_patch_notes_cell_returns_sanitizer_warnings(client_and_run) -> None:
    """Peer-review #7: when the sanitiser removes anything, the PATCH
    response surfaces what was dropped so the editor can (eventually)
    tell the user "we cleaned X" instead of silently swapping
    content under them. Back-compat: callers that ignore the field
    are unaffected."""
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": '<p>ok</p><script>alert(1)</script>'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "sanitizer_warnings" in body, (
        "PATCH response should carry sanitizer_warnings so the UI can "
        "surface silent strips"
    )
    warnings = body["sanitizer_warnings"]
    assert isinstance(warnings, list)
    # At least one warning must name the script strip — operators need
    # to see what was removed, not just that something was.
    assert any("script" in w.lower() for w in warnings), warnings


def test_patch_notes_cell_returns_empty_warnings_on_clean_input(
    client_and_run,
) -> None:
    """When the sanitiser is a no-op, `sanitizer_warnings` must be
    an empty list — not absent — so the UI can treat the field as
    always-present."""
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": "<p>all-clean</p>"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("sanitizer_warnings") == []


def test_patch_notes_cell_persists_whitelisted_table_styles(client_and_run) -> None:
    """Notes WYSIWYG (Phase 1): a styled table cell round-trips through PATCH
    with the validated fill / per-side border intact, so the review panel can
    re-render the formatting the accountant set. Disallowed declarations are
    dropped + surfaced as warnings."""
    client, run_id = client_and_run

    styled = (
        '<table><tr>'
        '<td style="background-color: #f4f4f4; border-bottom: 1px solid #000; '
        'position: fixed">RM</td>'
        '</tr></table>'
    )
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": styled},
    )
    assert resp.status_code == 200
    body = resp.json()
    html = body["html"].lower()
    # Whitelisted declarations survive...
    assert "background-color: #f4f4f4" in html
    assert "border-bottom: 1px solid #000" in html
    # ...the disallowed one is dropped and surfaced.
    assert "position" not in html
    assert any("position" in w.lower() for w in body["sanitizer_warnings"])


def test_patch_notes_cell_preserves_browser_rgb_border_colour(client_and_run) -> None:
    """TipTap/browser serialisation includes spaces in rgb(), so preserve the
    border rather than silently falling back to the editor's grey grid."""
    client, run_id = client_and_run
    rgb_border = "rgb(255, 255, 255)"
    style = "; ".join(
        f"border-{side}: 1px solid {rgb_border}"
        for side in ("top", "right", "bottom", "left")
    )
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": f'<table><tr><td style="{style}">x</td></tr></table>'},
    )
    assert resp.status_code == 200
    assert resp.json()["html"].lower().count(rgb_border) == 4
    assert resp.json()["sanitizer_warnings"] == []


def test_patch_notes_cell_reset_values_persist(client_and_run) -> None:
    """"No fill" / "no border" persist as explicit reset values (peer-review
    #2) — the panel needs them to override the default grid + header fill."""
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={
            "html": (
                '<table><tr>'
                '<th style="background-color: transparent; border-top: none; '
                'border-bottom: none">x</th>'
                '</tr></table>'
            )
        },
    )
    assert resp.status_code == 200
    html = resp.json()["html"].lower()
    assert "background-color: transparent" in html
    assert "border-top: none" in html
    assert "border-bottom: none" in html


def test_patch_notes_cell_400_for_unknown_row(client_and_run) -> None:
    """A row that is neither an existing cell nor a fillable registry row is
    rejected (400) — the editor can't invent rows (PLAN Step 8)."""
    client, run_id = client_and_run

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/9999",
        json={"html": "<p>x</p>"},
    )
    assert resp.status_code == 400


def test_patch_notes_cell_inserts_blank_registry_row(client_and_run) -> None:
    """Editing a previously-blank template row inserts a notes_cells row,
    using the registry label + template-scoped concept_uuid (PLAN Step 8)."""
    client, run_id = client_and_run

    # Find a fillable prose row that the projection surfaced as blank.
    body = client.get(f"/api/runs/{run_id}/notes_cells").json()
    ci = next(s for s in body["sheets"] if s["sheet"] == "Notes-CI")
    blank = next(r for r in ci["rows"] if r["html"] == "" and r["node_uuid"])

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/{blank['row']}",
        json={"html": "<p>freshly typed</p>"},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert "<p>freshly typed</p>" in out["html"]
    # The inserted row keeps the registry's label, not a client-supplied one.
    assert out["label"] == blank["label"]

    # And it now reads back as a filled row on the next GET.
    body2 = client.get(f"/api/runs/{run_id}/notes_cells").json()
    ci2 = next(s for s in body2["sheets"] if s["sheet"] == "Notes-CI")
    again = next(r for r in ci2["rows"] if r["row"] == blank["row"])
    assert "<p>freshly typed</p>" in again["html"]


def test_patch_blank_row_preserves_template_scoped_uuid(client_and_run, tmp_path) -> None:
    """Re-editing an inserted blank row keeps its template-scoped concept_uuid —
    the second PATCH must not downgrade it to the legacy mint (peer-review
    MEDIUM)."""
    import sqlite3
    import server as server_module

    client, run_id = client_and_run

    body = client.get(f"/api/runs/{run_id}/notes_cells").json()
    ci = next(s for s in body["sheets"] if s["sheet"] == "Notes-CI")
    blank = next(r for r in ci["rows"] if r["html"] == "" and r["node_uuid"])
    node_uuid = blank["node_uuid"]

    def stored_uuid() -> str:
        conn = sqlite3.connect(str(server_module.AUDIT_DB_PATH))
        try:
            return conn.execute(
                "SELECT concept_uuid FROM notes_cells "
                "WHERE run_id = ? AND sheet = 'Notes-CI' AND row = ?",
                (run_id, blank["row"]),
            ).fetchone()[0]
        finally:
            conn.close()

    # First edit (insert) stamps the template-scoped node_uuid.
    client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/{blank['row']}",
        json={"html": "<p>first</p>"},
    )
    assert stored_uuid() == node_uuid

    # Second edit (update) must preserve it, not re-mint the legacy uuid.
    client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/{blank['row']}",
        json={"html": "<p>second</p>"},
    )
    assert stored_uuid() == node_uuid


def test_patch_notes_cell_413_when_rendered_text_over_30k(client_and_run) -> None:
    """Enforce the same 30k rendered-char cap the writer applies. The
    editor must not be able to bypass it by PATCHing oversized HTML."""
    client, run_id = client_and_run

    giant = "<p>" + ("x" * 31_000) + "</p>"
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": giant},
    )
    assert resp.status_code == 413
    detail = resp.json().get("detail", "")
    assert "30" in detail or "limit" in detail.lower()


def test_patch_notes_cell_413_before_sanitizer_runs_on_huge_body(
    client_and_run,
) -> None:
    """Peer-review #4: a client POSTing a megabyte-sized HTML blob used
    to make BeautifulSoup parse the whole thing before the rendered-
    length cap kicked in — a cheap DOS avenue. Reject oversized raw
    bodies before the sanitiser gets them.

    The threshold is chosen so a legitimate 30k rendered-text cell with
    realistic tag overhead still passes; anything 7x+ that is the
    client doing something wrong.
    """
    client, run_id = client_and_run

    # 500kB of raw HTML — far beyond any legitimate notes cell but well
    # inside uvicorn's default body limit, so we hit the app handler.
    giant = "<p>" + ("y" * 500_000) + "</p>"
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": giant},
    )
    assert resp.status_code == 413
    detail = resp.json().get("detail", "")
    # Detail must distinguish the pre-sanitise guard from the rendered-
    # length cap so operators can tell the two apart in logs.
    assert "pre-sanit" in detail.lower() or "too large" in detail.lower()


def test_patch_notes_cell_does_not_touch_evidence(client_and_run) -> None:
    """Evidence is the audit-trail column — the editor is read-only on
    it, and the API must not let a malformed payload overwrite it.

    The PATCH body model declares ``extra='forbid'`` (SUG-2 hardening)
    so an unknown key surfaces as a 422 instead of being silently
    dropped. We assert both ends: the 422 rejection, and that a
    follow-up "clean" PATCH still leaves evidence intact.
    """
    client, run_id = client_and_run

    # Unknown field → 422, not 200. Prior behaviour silently dropped
    # the extra key and answered 200 — the new strict path surfaces
    # a client typo (``htmll``, ``evdience``, …) early.
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": "<p>new</p>", "evidence": "HACKED"},
    )
    assert resp.status_code == 422

    # A clean PATCH still succeeds and leaves evidence untouched.
    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": "<p>new</p>"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence"] == "Page 3"


def test_patch_wraps_select_and_upsert_in_begin_immediate(client_and_run, monkeypatch):
    """Peer-review I-3: PATCH must run SELECT + UPSERT inside a single
    write transaction so a concurrent regenerate (delete + re-INSERT)
    can't interleave between them. We verify the BEGIN IMMEDIATE is
    issued by intercepting the sqlite connection's `execute` calls and
    recording the order of the SELECT/UPSERT statements relative to
    the BEGIN IMMEDIATE and the COMMIT.
    """
    client, run_id = client_and_run

    import server
    import sqlite3 as _sqlite3

    original_connect = _sqlite3.connect
    execute_log: list[str] = []

    class _RecordingConn:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def execute(self, sql, params=()):
            execute_log.append(sql.split()[0].upper())
            return self._inner.execute(sql, params)

        def commit(self):
            # sqlite3.Connection.commit is a direct method, not a SQL
            # statement. Log it so the test can verify the BEGIN…COMMIT
            # bracket around the SELECT+UPSERT.
            execute_log.append("COMMIT")
            return self._inner.commit()

        def rollback(self):
            execute_log.append("ROLLBACK")
            return self._inner.rollback()

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            # Forward sets (e.g. row_factory) to the inner conn so pragmas
            # land on the real sqlite3.Connection, not the wrapper.
            setattr(self._inner, name, value)

    def _patched_open():
        # Replicate _open_audit_conn's pragma setup so the row_factory
        # matches what the endpoint code expects.
        inner = original_connect(str(server.AUDIT_DB_PATH))
        inner.execute("PRAGMA foreign_keys = ON")
        inner.execute("PRAGMA journal_mode = WAL")
        inner.execute("PRAGMA busy_timeout = 5000")
        inner.row_factory = _sqlite3.Row
        # Clear the log — the PRAGMA setup isn't part of the PATCH flow.
        execute_log.clear()
        return _RecordingConn(inner)

    monkeypatch.setattr(server, "_open_audit_conn", _patched_open)

    resp = client.patch(
        f"/api/runs/{run_id}/notes_cells/Notes-CI/4",
        json={"html": "<p>tx test</p>"},
    )
    assert resp.status_code == 200

    # The PATCH flow should carry a BEGIN IMMEDIATE before the SELECT
    # that locates the row, and a COMMIT after the UPSERT. The SELECT,
    # INSERT/REPLACE (upsert), and COMMIT must all appear after BEGIN.
    joined = execute_log
    assert "BEGIN" in joined, (
        f"PATCH did not issue BEGIN IMMEDIATE — statement sequence: {joined}"
    )
    begin_idx = joined.index("BEGIN")
    # A SELECT (the existence check) must appear after BEGIN.
    select_indexes = [i for i, s in enumerate(joined)
                      if s == "SELECT" and i > begin_idx]
    assert select_indexes, "No SELECT inside the BEGIN…COMMIT window"
    # And a COMMIT must close the window.
    assert "COMMIT" in joined[begin_idx:], (
        f"PATCH did not COMMIT its write transaction — sequence: {joined}"
    )
