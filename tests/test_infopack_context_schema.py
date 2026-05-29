"""Phase 2 Step 16 — Infopack context fields round-trip cleanly.

Verifies the new top-level fields (entity_name, reporting periods,
currency, scale_unit, consolidation_level), their safe defaults, and
backward compatibility with pre-Phase-2 payloads. The safety-critical
property is that ``scale_unit`` defaults to ``"unknown"`` — never a
guess — because a wrong unit produces a silent 1000× error downstream.
"""
from __future__ import annotations

import json

import pytest

from scout.infopack import Infopack


class TestDefaults:
    def test_safe_defaults_when_not_supplied(self):
        pack = Infopack(toc_page=2, page_offset=0)
        assert pack.entity_name is None
        assert pack.reporting_period_cy is None
        assert pack.reporting_period_py is None
        # Currency defaults to RM (Malaysian filings); scale + consolidation
        # default to "unknown" so the renderer's "VERIFY" block fires.
        assert pack.currency == "RM"
        assert pack.scale_unit == "unknown"
        assert pack.consolidation_level == "unknown"


class TestRoundTrip:
    def test_full_context_round_trips(self):
        original = Infopack(
            toc_page=2,
            page_offset=4,
            entity_name="FINCO Berhad",
            reporting_period_cy="01/01/2022 - 31/12/2022",
            reporting_period_py="01/01/2021 - 31/12/2021",
            currency="RM",
            scale_unit="thousands",
            consolidation_level="company",
        )
        restored = Infopack.from_json(original.to_json())
        assert restored.entity_name == "FINCO Berhad"
        assert restored.reporting_period_cy == "01/01/2022 - 31/12/2022"
        assert restored.reporting_period_py == "01/01/2021 - 31/12/2021"
        assert restored.currency == "RM"
        assert restored.scale_unit == "thousands"
        assert restored.consolidation_level == "company"

    def test_legacy_payload_without_context_fields(self):
        legacy = {
            "toc_page": 2,
            "page_offset": 0,
            "detected_standard": "unknown",
            "statements": {},
            "notes_inventory": [],
            # No entity_name / reporting_period* / scale_unit etc.
        }
        restored = Infopack.from_json(json.dumps(legacy))
        assert restored.entity_name is None
        # Crucially scale_unit defaults to "unknown", not a value the
        # renderer might trust.
        assert restored.scale_unit == "unknown"


class TestDefensiveCoercion:
    def test_bad_scale_unit_value_coerced_to_unknown(self):
        payload = {
            "toc_page": 2, "page_offset": 0, "detected_standard": "unknown",
            "statements": {}, "notes_inventory": [],
            "scale_unit": "thousands_of_millions",  # not a valid value
        }
        restored = Infopack.from_json(json.dumps(payload))
        assert restored.scale_unit == "unknown"

    def test_bad_consolidation_level_coerced_to_unknown(self):
        payload = {
            "toc_page": 2, "page_offset": 0, "detected_standard": "unknown",
            "statements": {}, "notes_inventory": [],
            "consolidation_level": "consolidated-and-also-the-other-thing",
        }
        restored = Infopack.from_json(json.dumps(payload))
        assert restored.consolidation_level == "unknown"

    def test_empty_string_entity_name_becomes_none(self):
        # Empty strings are not useful — the renderer would print an
        # awkward blank line. Coerce to None so the block omits cleanly.
        payload = {
            "toc_page": 2, "page_offset": 0, "detected_standard": "unknown",
            "statements": {}, "notes_inventory": [],
            "entity_name": "   ",
            "reporting_period_cy": "",
        }
        restored = Infopack.from_json(json.dumps(payload))
        assert restored.entity_name is None
        assert restored.reporting_period_cy is None
