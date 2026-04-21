"""Tests for PydanticAI scout agent.

The scout agent replaces the pipeline of one-shot LLM calls with a single
PydanticAI agent that sees PDF pages directly and uses deterministic tools
as cross-checks.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import fitz

from statement_types import StatementType


# ---------------------------------------------------------------------------
# Synthetic PDF fixture (reusable across all phases)
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    """Synthetic PDF with TOC + statement pages for agent testing."""
    doc = fitz.open()

    # Page 1: cover
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 100), "Annual Report 2021", fontsize=16)
    w.write_text(page)

    # Page 2: TOC
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Table of Contents", fontsize=16)
    w.append((72, 100), "Statement of Financial Position .......... 5", fontsize=11)
    w.append((72, 120), "Statement of Profit or Loss .......... 6", fontsize=11)
    w.append((72, 140), "Statement of Comprehensive Income .......... 7", fontsize=11)
    w.append((72, 160), "Statement of Cash Flows .......... 8", fontsize=11)
    w.append((72, 180), "Statement of Changes in Equity .......... 9", fontsize=11)
    w.append((72, 200), "Notes to the Financial Statements .......... 10", fontsize=11)
    w.write_text(page)

    # Pages 3-4: filler
    for _ in range(2):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 100), "Directors Report content", fontsize=11)
        w.write_text(page)

    # Page 5: SOFP (CuNonCu)
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Financial Position", fontsize=14)
    w.append((72, 100), "Non-current assets", fontsize=11)
    w.append((72, 120), "Property, plant and equipment  Note 4  1,234", fontsize=11)
    w.append((72, 140), "Current assets", fontsize=11)
    w.append((72, 160), "Trade receivables  Note 5  384", fontsize=11)
    w.append((72, 180), "Non-current liabilities", fontsize=11)
    w.write_text(page)

    # Page 6: SOPL (Function)
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Profit or Loss", fontsize=14)
    w.append((72, 100), "Revenue  10,000", fontsize=11)
    w.append((72, 120), "Cost of sales  (7,000)", fontsize=11)
    w.append((72, 140), "Administrative expenses  (1,000)", fontsize=11)
    w.write_text(page)

    # Page 7: SOCI
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Comprehensive Income", fontsize=14)
    w.append((72, 100), "Profit for the year  2,000", fontsize=11)
    w.append((72, 120), "Other comprehensive income before tax", fontsize=11)
    w.write_text(page)

    # Page 8: SOCF (Indirect)
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Cash Flows", fontsize=14)
    w.append((72, 100), "Profit before tax  2,500", fontsize=11)
    w.append((72, 120), "Adjustments for:", fontsize=11)
    w.append((72, 140), "Depreciation  500", fontsize=11)
    w.write_text(page)

    # Page 9: SOCIE
    page = doc.new_page()
    w = fitz.TextWriter(page.rect)
    w.append((72, 60), "Statement of Changes in Equity", fontsize=14)
    w.append((72, 100), "Share capital  Retained earnings  Total equity", fontsize=11)
    w.write_text(page)

    # Pages 10-12: notes
    for i in range(3):
        page = doc.new_page()
        w = fitz.TextWriter(page.rect)
        w.append((72, 60), f"Note {i + 4}", fontsize=14)
        w.append((72, 100), f"Details for note {i + 4}", fontsize=11)
        w.write_text(page)

    path = tmp_path / "synthetic_annual_report.pdf"
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Phase 1: Agent creation + ScoutDeps
# ---------------------------------------------------------------------------

class TestCreateScoutAgent:
    """Step 1/2: Agent factory returns correctly configured (Agent, ScoutDeps)."""

    def test_returns_agent_and_deps(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent, ScoutDeps

        agent, deps = create_scout_agent(
            pdf_path=synthetic_pdf,
            model="test",
        )
        assert agent is not None
        assert isinstance(deps, ScoutDeps)

    def test_deps_holds_state(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent, ScoutDeps

        agent, deps = create_scout_agent(
            pdf_path=synthetic_pdf,
            model="test",
            statements_to_find={StatementType.SOFP, StatementType.SOPL},
        )
        assert deps.pdf_path == synthetic_pdf
        assert deps.pdf_length > 0
        assert deps.statements_to_find == {StatementType.SOFP, StatementType.SOPL}
        # Mutable state starts empty
        assert deps.infopack is None

    def test_default_statements_is_all_five(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        assert deps.statements_to_find is None  # None means all 5

    def test_force_vision_inventory_defaults_false(self, synthetic_pdf: Path):
        """Scanned-PDF override is off unless the caller opts in."""
        from scout.agent import create_scout_agent

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        assert deps.force_vision_inventory is False

    def test_force_vision_inventory_accepts_true(self, synthetic_pdf: Path):
        """Caller can flag this run as a scanned PDF; deps record the intent."""
        from scout.agent import create_scout_agent

        _, deps = create_scout_agent(
            pdf_path=synthetic_pdf, model="test", force_vision_inventory=True,
        )
        assert deps.force_vision_inventory is True

    def test_derive_notes_start_page_prefers_smallest_note_page(self, synthetic_pdf: Path):
        """When statements have note_pages, notes section begins at the
        smallest note_page across all statements — that's the earliest
        page any statement references."""
        from scout.agent import _derive_notes_start_page
        from scout.infopack import Infopack, StatementPageRef

        infopack = Infopack(
            toc_page=1, page_offset=0,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu", face_page=10, note_pages=[20, 21, 25],
                ),
                StatementType.SOCF: StatementPageRef(
                    variant_suggestion="Indirect", face_page=13, note_pages=[18, 19],
                ),
            },
        )
        assert _derive_notes_start_page(infopack) == 18

    def test_derive_notes_start_page_falls_back_to_max_face_plus_one(self, synthetic_pdf: Path):
        """When no note_pages are populated, notes are assumed to begin
        right after the last face page."""
        from scout.agent import _derive_notes_start_page
        from scout.infopack import Infopack, StatementPageRef

        infopack = Infopack(
            toc_page=1, page_offset=0,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu", face_page=12, note_pages=[],
                ),
                StatementType.SOCF: StatementPageRef(
                    variant_suggestion="Indirect", face_page=15, note_pages=[],
                ),
            },
        )
        assert _derive_notes_start_page(infopack) == 16

    def test_derive_notes_start_page_returns_none_when_empty(self, synthetic_pdf: Path):
        """Empty statements dict → no way to infer; callers must not crash."""
        from scout.agent import _derive_notes_start_page
        from scout.infopack import Infopack

        assert _derive_notes_start_page(Infopack(toc_page=1, page_offset=0)) is None


class TestPopulateInventoryFallback:
    """Post-scout safety net: if the LLM never called
    ``discover_notes_inventory`` on a scanned PDF the operator flagged,
    run the vision pass ourselves before returning. Tests target the
    helper directly so we don't have to stand up a PydanticAI agent."""

    def _make_deps(self, pdf_path: Path, *, force: bool, has_model: bool):
        from scout.agent import ScoutDeps

        class _FakeModel:
            """Placeholder — deps treats any truthy vision_model as valid."""
        return ScoutDeps(
            pdf_path=pdf_path,
            pdf_length=12,
            statements_to_find=None,
            on_progress=None,
            vision_model=_FakeModel() if has_model else None,
            force_vision_inventory=force,
        )

    @pytest.mark.asyncio
    async def test_fires_when_inventory_empty_and_flag_and_model_present(
        self, synthetic_pdf: Path,
    ):
        from unittest.mock import patch
        from scout.agent import _populate_inventory_via_vision
        from scout.infopack import Infopack, StatementPageRef
        from scout.notes_discoverer import NoteInventoryEntry

        deps = self._make_deps(synthetic_pdf, force=True, has_model=True)
        infopack = Infopack(
            toc_page=1, page_offset=0,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu", face_page=10, note_pages=[20, 21],
                ),
            },
        )
        captured: dict = {}

        async def fake_build(**kwargs):
            captured.update(kwargs)
            return [NoteInventoryEntry(note_num=1, title="fallback", page_range=(20, 22))]

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=fake_build):
            await _populate_inventory_via_vision(infopack, deps)

        assert captured.get("force_vision") is True
        assert captured.get("notes_start_page") == 20
        assert [e.note_num for e in infopack.notes_inventory] == [1]

    @pytest.mark.asyncio
    async def test_noop_without_force_vision(self, synthetic_pdf: Path):
        from unittest.mock import patch
        from scout.agent import _populate_inventory_via_vision
        from scout.infopack import Infopack

        deps = self._make_deps(synthetic_pdf, force=False, has_model=True)
        infopack = Infopack(toc_page=1, page_offset=0)

        called = {"vision": False}

        async def _would_fail(**kwargs):
            called["vision"] = True
            return []

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=_would_fail):
            await _populate_inventory_via_vision(infopack, deps)

        assert called["vision"] is False
        assert infopack.notes_inventory == []

    @pytest.mark.asyncio
    async def test_noop_without_vision_model(self, synthetic_pdf: Path):
        """No Model available (e.g. scout invoked with a plain string for
        testing) — fallback must short-circuit, not crash."""
        from unittest.mock import patch
        from scout.agent import _populate_inventory_via_vision
        from scout.infopack import Infopack

        deps = self._make_deps(synthetic_pdf, force=True, has_model=False)
        infopack = Infopack(toc_page=1, page_offset=0)

        called = {"vision": False}

        async def _would_fail(**kwargs):
            called["vision"] = True
            return []

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=_would_fail):
            await _populate_inventory_via_vision(infopack, deps)

        assert called["vision"] is False

    @pytest.mark.asyncio
    async def test_noop_when_inventory_already_populated(self, synthetic_pdf: Path):
        from unittest.mock import patch
        from scout.agent import _populate_inventory_via_vision
        from scout.infopack import Infopack
        from scout.notes_discoverer import NoteInventoryEntry

        deps = self._make_deps(synthetic_pdf, force=True, has_model=True)
        infopack = Infopack(
            toc_page=1, page_offset=0,
            notes_inventory=[
                NoteInventoryEntry(note_num=1, title="already there", page_range=(10, 10)),
            ],
        )

        called = {"vision": False}

        async def _would_fail(**kwargs):
            called["vision"] = True
            return []

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=_would_fail):
            await _populate_inventory_via_vision(infopack, deps)

        assert called["vision"] is False
        assert [e.note_num for e in infopack.notes_inventory] == [1]

    def test_system_prompt_teaches_discover_notes_inventory(self):
        """The LLM must be told `discover_notes_inventory` exists and when to
        call it — otherwise scanned-PDF runs silently skip the tool and
        Sheet-12 fan-out fails with an empty inventory. Guard the prompt
        contract so prompt edits can't regress this."""
        from scout.agent import _SYSTEM_PROMPT

        assert "discover_notes_inventory" in _SYSTEM_PROMPT, (
            "system prompt must mention discover_notes_inventory so the LLM "
            "knows to call it; otherwise Sheet-12 runs with an empty inventory "
            "on scanned PDFs"
        )


class TestDiscoverNotesInventoryTool:
    """The tool must forward ScoutDeps.force_vision_inventory into the
    discoverer so an operator flag reaches PyMuPDF/vision logic."""

    def test_forwards_force_vision_flag(self, synthetic_pdf: Path):
        import asyncio
        from unittest.mock import patch
        from scout.agent import create_scout_agent, _discover_notes_inventory_impl

        _, deps = create_scout_agent(
            pdf_path=synthetic_pdf, model="test", force_vision_inventory=True,
        )

        captured: dict = {}

        async def fake_build(**kwargs):
            captured.update(kwargs)
            return []

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=fake_build):
            asyncio.run(_discover_notes_inventory_impl(deps, notes_start_page=10))

        assert captured.get("force_vision") is True

    def test_default_force_vision_false(self, synthetic_pdf: Path):
        import asyncio
        from unittest.mock import patch
        from scout.agent import create_scout_agent, _discover_notes_inventory_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")

        captured: dict = {}

        async def fake_build(**kwargs):
            captured.update(kwargs)
            return []

        with patch("scout.notes_discoverer.build_notes_inventory_async", side_effect=fake_build):
            asyncio.run(_discover_notes_inventory_impl(deps, notes_start_page=10))

        assert captured.get("force_vision") is False


# ---------------------------------------------------------------------------
# Phase 2: Deterministic tool tests
# ---------------------------------------------------------------------------

class TestFindTocTool:
    """Steps 3/4: find_toc tool wraps deterministic TOC detection."""

    def test_finds_toc_in_synthetic_pdf(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent
        from scout.agent import _find_toc_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        result = _find_toc_impl(deps)
        assert result["toc_page"] > 0
        # Should find at least SOFP, SOPL entries
        types_found = {e["type"] for e in result["entries"] if e["type"]}
        assert "SOFP" in types_found
        assert "SOPL" in types_found

    def test_returns_empty_entries_for_no_toc(self, tmp_path: Path):
        """PDF with no TOC text should return empty entries."""
        doc = fitz.open()
        for _ in range(5):
            doc.new_page()  # blank pages
        path = tmp_path / "no_toc.pdf"
        doc.save(str(path))
        doc.close()

        from scout.agent import create_scout_agent, _find_toc_impl

        _, deps = create_scout_agent(pdf_path=path, model="test")
        result = _find_toc_impl(deps)
        assert result["entries"] == []


class TestParseTocTextTool:
    """Step 5: parse_toc_text tool."""

    def test_parses_toc_text(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent, _parse_toc_text_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")

        text = """
Statement of Financial Position    8
Statement of Profit or Loss    10
Notes to the Financial Statements    18
"""
        result = _parse_toc_text_impl(deps, text)
        types_found = {e["type"] for e in result if e["type"]}
        assert "SOFP" in types_found
        assert "SOPL" in types_found
        # Should cache entries in deps for discover_notes
        assert len(deps.toc_entries) > 0


class TestCheckVariantSignalsTool:
    """Step 6: check_variant_signals tool."""

    def test_detects_cunoncu(self):
        from scout.agent import _check_variant_signals_impl

        result = _check_variant_signals_impl(
            "SOFP", "Non-current assets\nCurrent assets\nNon-current liabilities"
        )
        assert result["variant"] == "CuNonCu"

    def test_returns_none_for_ambiguous(self):
        from scout.agent import _check_variant_signals_impl

        result = _check_variant_signals_impl("SOCI", "random text with no signals")
        assert result["variant"] is None


class TestDiscoverNotesTool:
    """Step 7: discover_notes tool."""

    def test_discovers_notes(self):
        from scout.agent import _discover_notes_impl

        result = _discover_notes_impl(
            face_text="Property Note 4\nTrade receivables Note 5",
            notes_start_page=10,
            pdf_length=20,
            toc_entries=[],
        )
        assert isinstance(result, list)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Phase 3: Vision tool tests
# ---------------------------------------------------------------------------

class TestViewPagesTool:
    """Steps 8/9: view_pages renders PDF pages for the agent to see."""

    def test_returns_images_for_valid_pages(self, synthetic_pdf: Path, tmp_path: Path):
        from scout.agent import create_scout_agent, _view_pages_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        result = _view_pages_impl(deps, [1, 2])
        # Should contain page labels and image data
        has_image = any(hasattr(item, "data") for item in result)
        assert has_image, "Should return at least one BinaryContent image"

    def test_rejects_out_of_range(self, synthetic_pdf: Path, tmp_path: Path):
        from scout.agent import create_scout_agent, _view_pages_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        result = _view_pages_impl(deps, [999])
        # Should get an error message, not a crash
        assert any("invalid" in str(item).lower() or "skipped" in str(item).lower()
                    for item in result)

    def test_includes_text_alongside_image(self, synthetic_pdf: Path, tmp_path: Path):
        from scout.agent import create_scout_agent, _view_pages_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        result = _view_pages_impl(deps, [5])  # SOFP page
        text_items = [item for item in result if isinstance(item, str)]
        # Should have page text that includes "Financial Position"
        combined = " ".join(text_items)
        assert "Financial Position" in combined or "Page 5" in combined

    def test_caps_at_max_pages(self, synthetic_pdf: Path, tmp_path: Path):
        from scout.agent import create_scout_agent, _view_pages_impl, MAX_VIEW_PAGES

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        all_pages = list(range(1, deps.pdf_length + 1))
        result = _view_pages_impl(deps, all_pages)
        # Count BinaryContent items (images)
        image_count = sum(1 for item in result if hasattr(item, "data"))
        assert image_count <= MAX_VIEW_PAGES

    def test_uses_in_memory_renderer(self, synthetic_pdf: Path, tmp_path: Path, monkeypatch):
        from scout.agent import create_scout_agent, _view_pages_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")

        def fail_disk_render(*args, **kwargs):
            raise AssertionError("disk renderer should not be used")

        monkeypatch.setattr("tools.pdf_viewer.render_pages_to_images", fail_disk_render)
        monkeypatch.setattr(
            "scout.agent.render_pages_to_png_bytes",
            lambda *args, **kwargs: [b"fake-png"],
        )

        result = _view_pages_impl(deps, [5])
        assert any(hasattr(item, "data") for item in result)


# ---------------------------------------------------------------------------
# Phase 4: save_infopack tool tests
# ---------------------------------------------------------------------------

class TestSaveInfopackTool:
    """Step 10: save_infopack persists the agent's result."""

    def test_valid_infopack_stored(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent, _save_infopack_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        infopack_data = {
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                          "note_pages": [10, 11], "confidence": "HIGH"},
            },
        }
        result = _save_infopack_impl(deps, json.dumps(infopack_data))
        assert "saved" in result.lower() or "success" in result.lower()
        assert deps.infopack is not None

    def test_invalid_page_raises(self, synthetic_pdf: Path):
        from scout.agent import create_scout_agent, _save_infopack_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        infopack_data = {
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 999,
                          "note_pages": [], "confidence": "HIGH"},
            },
        }
        result = _save_infopack_impl(deps, json.dumps(infopack_data))
        assert "error" in result.lower() or "invalid" in result.lower() or "exceed" in result.lower()

    def test_hallucinated_variant_rejected(self, synthetic_pdf: Path):
        """Variant names not in the registry should be rejected."""
        from scout.agent import create_scout_agent, _save_infopack_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        infopack_data = {
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNoCu", "face_page": 5,
                          "note_pages": [], "confidence": "HIGH"},
            },
        }
        result = _save_infopack_impl(deps, json.dumps(infopack_data))
        assert "error" in result.lower()
        assert "unknown variant" in result.lower()
        assert deps.infopack is None

    def test_not_prepared_variant_rejected(self, synthetic_pdf: Path):
        """NotPrepared (meta-variant, no template) should be rejected."""
        from scout.agent import create_scout_agent, _save_infopack_impl

        _, deps = create_scout_agent(pdf_path=synthetic_pdf, model="test")
        infopack_data = {
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOCI": {"variant_suggestion": "NotPrepared", "face_page": 7,
                          "note_pages": [], "confidence": "HIGH"},
            },
        }
        result = _save_infopack_impl(deps, json.dumps(infopack_data))
        assert "error" in result.lower()
        assert "no template" in result.lower()

    def test_extra_statements_filtered(self, synthetic_pdf: Path):
        """Statements outside statements_to_find should be silently dropped."""
        from scout.agent import create_scout_agent, _save_infopack_impl

        _, deps = create_scout_agent(
            pdf_path=synthetic_pdf,
            model="test",
            statements_to_find={StatementType.SOFP},
        )
        infopack_data = {
            "toc_page": 2,
            "page_offset": 0,
            "statements": {
                "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                          "note_pages": [], "confidence": "HIGH"},
                "SOPL": {"variant_suggestion": "Function", "face_page": 6,
                          "note_pages": [], "confidence": "HIGH"},
            },
        }
        result = _save_infopack_impl(deps, json.dumps(infopack_data))
        assert "success" in result.lower()
        # Only SOFP should be in the infopack
        assert StatementType.SOFP in deps.infopack.statements
        assert StatementType.SOPL not in deps.infopack.statements


# ---------------------------------------------------------------------------
# Phase 5: End-to-end agent run
# ---------------------------------------------------------------------------

class TestRunScoutAgent:
    """Steps 11/12: run_scout() using the PydanticAI agent."""

    @pytest.mark.asyncio
    async def test_run_scout_returns_infopack(self, synthetic_pdf: Path):
        """run_scout() should return a valid Infopack using the agent."""
        from scout.agent import run_scout
        from scout.infopack import Infopack
        from unittest.mock import AsyncMock, patch
        from pydantic_ai.models.function import FunctionModel, AgentInfo
        from pydantic_ai.messages import (
            ModelResponse, ToolCallPart, TextPart,
        )

        # Track tool calls to simulate a realistic agent flow:
        # 1. find_toc  2. view_pages(5)  3. save_infopack
        call_count = 0

        def model_function(messages, info: AgentInfo):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # Agent's first move: call find_toc
                return ModelResponse(parts=[
                    ToolCallPart(tool_name="find_toc", args={}, tool_call_id="tc1"),
                ])
            elif call_count == 2:
                # After seeing TOC, save the infopack
                infopack_data = {
                    "toc_page": 2,
                    "page_offset": 0,
                    "statements": {
                        "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                                  "note_pages": [10], "confidence": "HIGH"},
                        "SOPL": {"variant_suggestion": "Function", "face_page": 6,
                                  "note_pages": [], "confidence": "HIGH"},
                    },
                }
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="save_infopack",
                        args={"infopack_json": json.dumps(infopack_data)},
                        tool_call_id="tc2",
                    ),
                ])
            else:
                # Done — return final text
                return ModelResponse(parts=[
                    TextPart(content="Scouting complete."),
                ])

        function_model = FunctionModel(model_function)

        infopack = await run_scout(
            pdf_path=synthetic_pdf,
            model=function_model,
            statements_to_find={StatementType.SOFP, StatementType.SOPL},
        )

        assert isinstance(infopack, Infopack)
        assert StatementType.SOFP in infopack.statements
        assert StatementType.SOPL in infopack.statements
        assert infopack.statements[StatementType.SOFP].face_page == 5
        assert infopack.statements[StatementType.SOFP].variant_suggestion == "CuNonCu"

    @pytest.mark.asyncio
    async def test_run_scout_progress_callback(self, synthetic_pdf: Path):
        """run_scout() should call on_progress during the run."""
        from scout.agent import run_scout
        from pydantic_ai.models.function import FunctionModel, AgentInfo
        from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart

        call_count = 0
        progress_messages = []

        async def on_progress(msg: str):
            progress_messages.append(msg)

        def model_function(messages, info: AgentInfo):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ModelResponse(parts=[
                    ToolCallPart(tool_name="find_toc", args={}, tool_call_id="tc1"),
                ])
            elif call_count == 2:
                infopack_data = {
                    "toc_page": 2, "page_offset": 0,
                    "statements": {
                        "SOFP": {"variant_suggestion": "CuNonCu", "face_page": 5,
                                  "note_pages": [], "confidence": "HIGH"},
                    },
                }
                return ModelResponse(parts=[
                    ToolCallPart(
                        tool_name="save_infopack",
                        args={"infopack_json": json.dumps(infopack_data)},
                        tool_call_id="tc2",
                    ),
                ])
            else:
                return ModelResponse(parts=[TextPart(content="Done.")])

        infopack = await run_scout(
            pdf_path=synthetic_pdf,
            model=FunctionModel(model_function),
            on_progress=on_progress,
        )
        assert infopack is not None
        # Should have received at least one progress message
        assert len(progress_messages) >= 1
