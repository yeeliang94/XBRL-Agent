"""Unit-aware value translation for the mTool fill — peer-review finding 2.

The defect these tests exist to prevent: the exporter carried ONE filing-wide
``scale`` multiplier with no unit dimension, so a thousands conversion would
multiply a share COUNT by 1,000 and file it. MFRS sheet 13
(``Notes-IssuedCapital``) is where that bites in practice — it carries share
counts and money amounts on the same sheet, a few rows apart.

Each unit class is exercised INDEPENDENTLY (plan Step 6's verify clause): a
thousands filing must scale the monetary fact and leave the share count
untouched, proven by assertion.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from concept_model.importer import import_company_targets, import_template
from concept_model.parser import parse_template
from db.schema import init_db
from mtool.exporter import build_fill_doc
from mtool.translation import (
    IDENTITY, TranslationManifest, UnitRule, UnknownUnitClass,
    manifest_by_version, thousands_manifest)
from mtool.units import (
    MONETARY, PER_SHARE, PURE, SHARES, load_unit_index, unit_class_for_label)

REPO = Path(__file__).resolve().parent.parent
ISSUED_CAPITAL = REPO / "XBRL-template-MFRS" / "Company" / "13-Notes-IssuedCapital.xlsx"

# The two rows the finding turns on, both LEAF on the same sheet.
SHARES_LABEL = "*Number of shares issued and fully paid"
MONEY_LABEL = "Balance at the beginning of period"


# ------------------------------------------------------- the index itself

def test_index_types_the_two_rows_finding_2_turns_on():
    assert unit_class_for_label(SHARES_LABEL, "mfrs") == SHARES
    assert unit_class_for_label(MONEY_LABEL, "mfrs") == MONETARY


def test_index_is_label_normalised():
    # The leading '*' (mandatory-row marker) and case are template rendering,
    # not identity — the taxonomy join must see through them.
    assert (unit_class_for_label("Number of shares issued and fully paid",
                                 "mfrs") == SHARES)
    assert (unit_class_for_label("*NUMBER OF SHARES ISSUED AND FULLY PAID",
                                 "mfrs") == SHARES)


def test_unknown_label_is_none_not_a_guess():
    assert unit_class_for_label("Definitely not an SSM concept", "mfrs") is None
    assert unit_class_for_label("", "mfrs") is None
    assert unit_class_for_label(MONEY_LABEL, "klingon") is None


def test_both_standards_ship_an_index():
    for standard in ("mfrs", "mpers"):
        index = load_unit_index(standard)
        assert len(index) > 1000, standard
        assert MONETARY in index.values()


def test_ambiguous_entries_read_as_unknown():
    """A label two NUMERIC concepts share is recorded ambiguous, and the
    runtime must treat that exactly like 'unknown' — never pick one."""
    index = load_unit_index("mfrs")
    ambiguous = [k for k, v in index.items() if v == "ambiguous"]
    for label in ambiguous[:5]:
        assert unit_class_for_label(label, "mfrs") is None


# ------------------------------------------------------- the manifest

def test_identity_manifest_changes_nothing():
    assert IDENTITY.is_identity
    assert IDENTITY.translate(1234.5, unit_class=MONETARY,
                              label=MONEY_LABEL) == 1234.5
    # …including for a row whose unit class we don't know: with no rule able
    # to change anything, a taxonomy gap is not a risk.
    assert IDENTITY.translate(7.0, unit_class=None, label="mystery") == 7.0


@pytest.mark.parametrize("unit_class,expected", [
    (MONETARY, 1_500_000),
    (SHARES, 1500),
    (PER_SHARE, 1500),
    (PURE, 1500),
])
def test_thousands_manifest_scales_only_money(unit_class, expected):
    """Each class independently — the assertion the plan asks for."""
    m = thousands_manifest()
    assert not m.is_identity
    assert m.translate(1500, unit_class=unit_class, label="x") == expected


def test_non_identity_manifest_refuses_an_unknown_unit():
    m = thousands_manifest()
    with pytest.raises(UnknownUnitClass) as exc:
        m.translate(1500, unit_class=None, label="Mystery row",
                    sheet="SOFP-CuNonCu")
    # The message names the row and the sheet, not a concept uuid.
    assert "Mystery row" in str(exc.value)
    assert "SOFP-CuNonCu" in str(exc.value)
    assert "Refusing to guess" in str(exc.value)


def test_missing_rule_for_a_known_class_also_refuses():
    """Loud on a rule GAP too, not just an unknown unit (plan Step 6)."""
    partial = TranslationManifest(
        version="partial-test",
        units={MONETARY: UnitRule(scale=1000.0)})
    assert partial.translate(1, unit_class=MONETARY, label="m") == 1000
    with pytest.raises(UnknownUnitClass):
        partial.translate(1, unit_class=SHARES, label="s")


def test_concept_override_beats_the_class_rule():
    from mtool.translation import ConceptOverride

    m = TranslationManifest(
        version="override-test",
        units={MONETARY: UnitRule(scale=1000.0)},
        overrides=(ConceptOverride(
            template_id="mfrs-company-09-socie",
            label_normalized="dividends paid",
            rule=UnitRule(sign=-1),
            evidence="hypothetical — exercises the slot, ADR-002 ships none"),))
    assert m.translate(
        5, unit_class=MONETARY, label="Dividends paid",
        template_id="mfrs-company-09-socie",
        label_normalized="dividends paid") == -5


def test_shipped_manifest_carries_no_overrides():
    """The override slot ships EMPTY — an override without evidence is the
    guess this layer exists to prevent."""
    assert IDENTITY.overrides == ()


def test_manifest_lookup_falls_back_to_identity():
    assert manifest_by_version(None) is IDENTITY
    assert manifest_by_version("no-such-version") is IDENTITY
    assert manifest_by_version("identity-1") is IDENTITY


def test_manifest_serialises_for_the_receipt():
    payload = json.loads(json.dumps(thousands_manifest().to_json()))
    assert payload["version"] == "thousands-1"
    assert payload["units"]["monetary"] == {"scale": 1000.0, "sign": 1}
    assert payload["units"]["shares"] == {"scale": 1.0, "sign": 1}


# ------------------------------------------------------- end to end

def _seed(db, run_id, uuid, value):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO run_concept_facts("
            "run_id, concept_uuid, period, entity_scope, value, value_status, "
            "updated_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, uuid, "CY", "Company", value, "observed",
             "2026-07-05T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()


def _uuid_for(db, label):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT concept_uuid FROM concept_nodes WHERE canonical_label = ? "
            "AND kind = 'LEAF'", (label,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no LEAF labelled {label!r}"
    return row[0]


def _make_unknown_unit_leaf(db) -> tuple[str, str]:
    """Keep the unit-gap behavior pinned without treating a heading as data."""
    label = "Unclassified issued capital measure"
    uuid = _uuid_for(db, SHARES_LABEL)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE concept_nodes SET canonical_label = ? WHERE concept_uuid = ?",
            (label, uuid),
        )
        conn.commit()
    finally:
        conn.close()
    return uuid, label


@pytest.fixture
def issued_capital_db(tmp_path: Path):
    db = tmp_path / "xbrl.db"
    init_db(db)
    tree = parse_template(str(ISSUED_CAPITAL))
    jp = tmp_path / "tree.json"
    jp.write_text(json.dumps(tree.to_json(), sort_keys=True), encoding="utf-8")
    tid = import_template(db, jp)
    import_company_targets(db, tid)
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "INSERT INTO runs(created_at, pdf_filename, status, started_at) "
            "VALUES (?,?,?,?)",
            ("2026-07-27T00:00:00Z", "x.pdf", "completed",
             "2026-07-27T00:00:00Z"))
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return db, run_id


def test_thousands_fill_scales_money_and_leaves_share_count_alone(
        issued_capital_db):
    """The finding-2 regression, end to end on the real MFRS sheet 13.

    10,000,000 shares in issue, RM 5,000 thousand of paid capital. Under a
    thousands manifest the money becomes 5,000,000 and the share count stays
    10,000,000 — the old blanket multiplier would have filed 10 billion shares.
    """
    db, run_id = issued_capital_db
    _seed(db, run_id, _uuid_for(db, SHARES_LABEL), 10_000_000)
    _seed(db, run_id, _uuid_for(db, MONEY_LABEL), 5_000)

    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company",
                         manifest=thousands_manifest())
    by_label = {w["label"]: w["value"] for w in doc["writes"]}
    assert by_label[SHARES_LABEL] == 10_000_000
    assert by_label[MONEY_LABEL] == 5_000_000
    assert doc["meta"]["translation_version"] == "thousands-1"
    assert doc["meta"]["unit_classes"] == {SHARES: 1, MONETARY: 1}


def test_identity_fill_emits_both_verbatim(issued_capital_db):
    db, run_id = issued_capital_db
    _seed(db, run_id, _uuid_for(db, SHARES_LABEL), 10_000_000)
    _seed(db, run_id, _uuid_for(db, MONEY_LABEL), 5_000)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    by_label = {w["label"]: w["value"] for w in doc["writes"]}
    assert by_label == {SHARES_LABEL: 10_000_000, MONEY_LABEL: 5_000}


def test_unknown_unit_rows_are_reported_not_hidden(issued_capital_db):
    """Under identity they pass through — but the operator still sees which
    rows have no unit class, because those are exactly the rows a future
    non-identity manifest would refuse."""
    db, run_id = issued_capital_db
    unknown_uuid, unknown_label = _make_unknown_unit_leaf(db)
    _seed(db, run_id, unknown_uuid, 1)
    doc = build_fill_doc(db, run_id, filing_standard="mfrs",
                         filing_level="company")
    unknown = doc["meta"]["unit_class_unknown"]
    assert [u["label"] for u in unknown] == [unknown_label]
    assert doc["meta"]["unit_classes"]["unknown"] == 1
    assert doc["writes"][0]["value"] == 1  # identity passes it through


def test_non_identity_fill_raises_on_an_unknown_unit(issued_capital_db):
    db, run_id = issued_capital_db
    unknown_uuid, _ = _make_unknown_unit_leaf(db)
    _seed(db, run_id, unknown_uuid, 1)
    with pytest.raises(UnknownUnitClass):
        build_fill_doc(db, run_id, filing_standard="mfrs",
                       filing_level="company",
                       manifest=thousands_manifest())
