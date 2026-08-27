"""The committed all-template semantics audit stays reproducible."""
from __future__ import annotations

import json
from pathlib import Path

from concept_model.filing_targets import audit_active_templates


ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "concept_model/template_field_semantics_audit.json"


def test_committed_template_semantics_snapshot_matches_live_templates():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert audit_active_templates(ROOT) == expected
