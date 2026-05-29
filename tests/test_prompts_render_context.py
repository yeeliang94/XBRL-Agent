"""Phase 2 Step 18 — context block renders with loud VERIFY wording.

Covers:
- Block omitted entirely when scout enrichment is absent (no regression)
- Block renders with each populated field
- Scale-unit "thousands" / "millions" carry the 1000× error warning
- Scale-unit "unknown" carries an even louder warning
- Block appears in render_prompt's full assembly
- The same block appears in render_notes_prompt output
"""
from __future__ import annotations

from statement_types import StatementType
from prompts import render_prompt, _render_scout_context_block
from notes_types import NotesTemplateType
from notes.agent import render_notes_prompt


class TestRenderBlockDirectly:
    def test_omitted_when_nothing_populated(self):
        assert _render_scout_context_block({}) == ""
        assert _render_scout_context_block({
            "entity_name": None, "reporting_period_cy": None,
            "reporting_period_py": None, "currency": "RM",
            "scale_unit": "unknown", "consolidation_level": "unknown",
        }) == ""

    def test_renders_all_populated_fields(self):
        block = _render_scout_context_block({
            "entity_name": "FINCO Berhad",
            "reporting_period_cy": "01/01/2022 - 31/12/2022",
            "reporting_period_py": "01/01/2021 - 31/12/2021",
            "currency": "RM",
            "scale_unit": "thousands",
            "consolidation_level": "company",
        })
        assert "SCOUT-OBSERVED CONTEXT (VERIFY EACH BEFORE USING)" in block
        assert "FINCO Berhad" in block
        assert "01/01/2022 - 31/12/2022" in block
        assert "01/01/2021 - 31/12/2021" in block
        assert "thousands (RM '000)" in block
        # The 1000× error warning is the load-bearing one.
        assert "1000×" in block
        # "VERIFY" appears multiple times across the block.
        assert block.count("VERIFY") >= 2

    def test_unknown_scale_unit_carries_louder_warning(self):
        block = _render_scout_context_block({
            "entity_name": "FINCO Berhad",
            "scale_unit": "unknown",
        })
        # "UNKNOWN" + "1000× error" + "MUST read" — the loudest variant.
        assert "UNKNOWN" in block
        assert "MUST read" in block
        assert "1000×" in block

    def test_currency_omitted_when_RM(self):
        # RM is the Malaysian default — don't clutter the prompt with
        # the obvious case. Non-RM currencies do render.
        block = _render_scout_context_block({
            "entity_name": "X", "currency": "RM",
        })
        assert "Currency" not in block

        block_usd = _render_scout_context_block({
            "entity_name": "X", "currency": "USD",
        })
        assert "Currency: USD" in block_usd

    def test_consolidation_level_omitted_when_unknown(self):
        block = _render_scout_context_block({
            "entity_name": "X", "consolidation_level": "unknown",
        })
        assert "Consolidation level" not in block

        block_group = _render_scout_context_block({
            "entity_name": "X", "consolidation_level": "group",
        })
        assert "Consolidation level: group" in block_group


class TestFullPromptAssembly:
    def test_face_prompt_includes_context_block(self):
        prompt = render_prompt(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
            scout_context={
                "entity_name": "FINCO Berhad",
                "scale_unit": "thousands",
            },
        )
        assert "SCOUT-OBSERVED CONTEXT" in prompt
        assert "FINCO Berhad" in prompt
        assert "1000×" in prompt

    def test_face_prompt_omits_block_when_no_context(self):
        prompt = render_prompt(
            statement_type=StatementType.SOFP,
            variant="CuNonCu",
        )
        # Today's prompt should be entirely unchanged on a no-context run.
        assert "SCOUT-OBSERVED CONTEXT" not in prompt

    def test_notes_prompt_includes_context_block(self):
        prompt = render_notes_prompt(
            template_type=NotesTemplateType.CORP_INFO,
            filing_level="company",
            inventory=[],
            scout_context={
                "entity_name": "FINCO Berhad",
                "reporting_period_cy": "01/01/2022 - 31/12/2022",
                "scale_unit": "thousands",
            },
        )
        assert "SCOUT-OBSERVED CONTEXT" in prompt
        assert "FINCO Berhad" in prompt
        assert "1000×" in prompt

    def test_notes_prompt_omits_block_when_no_context(self):
        prompt = render_notes_prompt(
            template_type=NotesTemplateType.CORP_INFO,
            filing_level="company",
            inventory=[],
        )
        # No scout_context = no block. Regression-safe.
        assert "SCOUT-OBSERVED CONTEXT" not in prompt
