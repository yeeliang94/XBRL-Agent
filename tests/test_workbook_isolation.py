"""Tests for per-statement workbook isolation (Step 4.4)."""

import json
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pydantic_ai.models.test import TestModel

from statement_types import StatementType
from extraction.agent import create_extraction_agent, ExtractionDeps


class TestWorkbookIsolation:
    """Each sub-agent writes to its own workbook file."""

    def test_output_filename_includes_statement_type(self):
        """The filled workbook should be named {statement_type}_filled.xlsx."""
        agent, deps = create_extraction_agent(
            statement_type=StatementType.SOPL,
            variant="Function",
            pdf_path="/tmp/test.pdf",
            template_path="/tmp/test.xlsx",
            model=TestModel(),
            output_dir="/tmp/output",
        )
        # The expected fill path pattern
        expected_name = "SOPL_filled.xlsx"
        assert deps.filled_filename == expected_name

    def test_each_statement_gets_unique_filename(self):
        """No two statement types should produce the same output filename."""
        filenames = set()
        for stmt in StatementType:
            from statement_types import variants_for
            variant = variants_for(stmt)[0].name
            agent, deps = create_extraction_agent(
                statement_type=stmt,
                variant=variant,
                pdf_path="/tmp/test.pdf",
                template_path="/tmp/test.xlsx",
                model=TestModel(),
                output_dir="/tmp/output",
            )
            filenames.add(deps.filled_filename)
        assert len(filenames) == 5, f"Expected 5 unique filenames, got {filenames}"

    def test_concurrent_agents_no_file_collision(self, tmp_path):
        """Three agents writing to the same output_dir produce 3 separate files."""
        import openpyxl

        # Create a minimal template
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "Test"
        template_path = str(tmp_path / "template.xlsx")
        wb.save(template_path)

        output_dir = str(tmp_path / "output")
        Path(output_dir).mkdir()

        agents_deps = []
        for stmt, variant in [
            (StatementType.SOFP, "CuNonCu"),
            (StatementType.SOPL, "Function"),
            (StatementType.SOCF, "Indirect"),
        ]:
            agent, deps = create_extraction_agent(
                statement_type=stmt,
                variant=variant,
                pdf_path="/tmp/test.pdf",
                template_path=template_path,
                model=TestModel(),
                output_dir=output_dir,
            )
            agents_deps.append((agent, deps))

        # Simulate each agent writing its workbook
        from tools.fill_workbook import fill_workbook as fill_impl

        for agent, deps in agents_deps:
            out_path = str(Path(deps.output_dir) / deps.filled_filename)
            result = fill_impl(
                template_path=template_path,
                output_path=out_path,
                fields_json='{"fields": []}',
            )
            assert result.success

        # Verify 3 separate files exist
        output_files = list(Path(output_dir).glob("*_filled.xlsx"))
        assert len(output_files) == 3
        file_names = {f.name for f in output_files}
        assert "SOFP_filled.xlsx" in file_names
        assert "SOPL_filled.xlsx" in file_names
        assert "SOCF_filled.xlsx" in file_names
