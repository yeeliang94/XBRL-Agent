"""Template re-import retires obsolete routing identities without data loss.

Server startup imports every managed template into the persistent audit DB.
A rename, row move, or removal under the same stable template_id must therefore
replace the *current* routing vocabulary while keeping historical run facts
readable through the concept UUID that the run originally stored.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from concept_model.cell_resolver import resolve_cell
from concept_model.facts_api import FactWrite, apply_fact, read_run_facts
from concept_model.importer import import_company_targets, import_template
from concept_model.label_resolver import resolve_label
from cross_checks.facts_util import read_labelled_value_last
from db import repository as repo
from db.schema import init_db
from statement_types import StatementType


_TEMPLATE_ID = "mfrs-company-lifecycle-v1"
_OLD_UUID = "11111111-1111-5111-8111-111111111111"
_NEW_UUID = "22222222-2222-5222-8222-222222222222"


def _concept(concept_uuid: str, *, label: str, row: int) -> dict:
    return {
        "concept_uuid": concept_uuid,
        "parent_uuid": None,
        "kind": "LEAF",
        "canonical_label": label,
        "render_key": {"sheet": "SOFP", "row": row, "col": "B"},
    }


def _import(db: Path, payload_path: Path, concepts: list[dict]) -> None:
    payload_path.write_text(
        json.dumps(
            {
                "template_id": _TEMPLATE_ID,
                "shape": "linear",
                "concepts": concepts,
            }
        ),
        encoding="utf-8",
    )
    template_id = import_template(db, payload_path)
    import_company_targets(db, template_id)


def _store_historical_fact(db: Path) -> int:
    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "historical.pdf")
        apply_fact(
            conn,
            run_id,
            FactWrite(
                concept_uuid=_OLD_UUID,
                period="CY",
                entity_scope="Company",
                value=125.0,
                value_status="observed",
            ),
        )
    return run_id


def _assert_historical_fact_is_readable(db: Path, run_id: int) -> None:
    with sqlite3.connect(db) as conn:
        facts = read_run_facts(conn, run_id, [_TEMPLATE_ID])
    assert facts[(_OLD_UUID, "CY", "Company")]["value"] == 125.0


@pytest.mark.parametrize(
    ("replacement", "expected_new_cell"),
    [
        (
            [_concept(_NEW_UUID, label="Cash and cash equivalents", row=10)],
            (10, _NEW_UUID),
        ),
        (
            [_concept(_NEW_UUID, label="Cash", row=14)],
            (14, _NEW_UUID),
        ),
        ([], None),
    ],
    ids=("rename", "move", "removal"),
)
def test_reimport_retires_obsolete_current_identity_but_keeps_historical_fact(
    tmp_path: Path,
    replacement: list[dict],
    expected_new_cell: tuple[int, str] | None,
) -> None:
    db = tmp_path / "audit.db"
    payload = tmp_path / "tree.json"
    init_db(db)
    _import(db, payload, [_concept(_OLD_UUID, label="Cash", row=10)])
    run_id = _store_historical_fact(db)

    _import(db, payload, replacement)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if expected_new_cell is None:
            assert resolve_label(conn, _TEMPLATE_ID, "Cash") is None
        else:
            new_row, new_uuid = expected_new_cell
            assert resolve_cell(conn, _TEMPLATE_ID, "SOFP", new_row, 2) == (
                new_uuid,
                "CY",
                "Company",
            )
        if expected_new_cell is None or expected_new_cell[0] != 10:
            assert resolve_cell(conn, _TEMPLATE_ID, "SOFP", 10, 2) is None

    _assert_historical_fact_is_readable(db, run_id)


def test_current_consumers_do_not_select_retired_identity(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    payload = tmp_path / "tree.json"
    init_db(db)
    _import(db, payload, [_concept(_OLD_UUID, label="Cash", row=20)])
    _import(db, payload, [_concept(_NEW_UUID, label="Cash", row=10)])

    with repo.db_session(db) as conn:
        run_id = repo.create_run(conn, "current.pdf")
        apply_fact(
            conn,
            run_id,
            FactWrite(
                concept_uuid=_NEW_UUID,
                period="CY",
                entity_scope="Company",
                value=250.0,
                value_status="observed",
            ),
        )

    with sqlite3.connect(db) as conn:
        ctx = SimpleNamespace(
            conn=conn,
            run_id=run_id,
            template_ids={StatementType.SOCF: _TEMPLATE_ID},
        )
        value = read_labelled_value_last(
            ctx, StatementType.SOCF, "Cash", "CY", "Company",
        )
        from eval.ingest import _gradeable_kinds
        from eval.mtool_ingest import build_catalogue

        kinds = _gradeable_kinds(conn, [_TEMPLATE_ID])
        catalogue = build_catalogue(
            conn, "mfrs", "company", [_TEMPLATE_ID],
        )

    assert value.value == 250.0
    assert value.row == 10
    assert _OLD_UUID not in kinds
    assert kinds[_NEW_UUID] == "LEAF"
    assert catalogue["SOFP"]["cash"].concept_uuid == _NEW_UUID
