"""Tests for the Python coordinator (Step 4.5)."""

import asyncio
import pytest
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional, Dict, Set, List
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from statement_types import StatementType


def _make_mock_agent_iter(mock_result=None, side_effect=None):
    """Build a mock agent whose .iter() async context manager either yields
    a successful empty run or raises side_effect on iteration.

    The mock simulates agent.iter() → AgentRun with no nodes (immediate completion).
    """
    mock_agent = MagicMock()

    if side_effect:
        # Make iter() context manager raise on entry
        @asynccontextmanager
        async def failing_iter(*args, **kwargs):
            raise side_effect
            yield  # pragma: no cover — unreachable, but needed for generator syntax
        mock_agent.iter = failing_iter
    else:
        # Make iter() return an AgentRun that yields no nodes (agent completes immediately)
        mock_run = MagicMock()
        mock_run.result = mock_result or MagicMock(output="done")
        mock_run.usage = MagicMock(return_value=MagicMock(
            request_tokens=100, response_tokens=50, total_tokens=150,
        ))

        # __aiter__ yields nothing — simulates an agent that completes without tool calls
        async def empty_aiter(self_ignored=None):
            return
            yield  # pragma: no cover — makes this an async generator
        mock_run.__aiter__ = empty_aiter

        @asynccontextmanager
        async def success_iter(*args, **kwargs):
            yield mock_run
        mock_agent.iter = success_iter

    return mock_agent


@dataclass
class RunConfig:
    """Configuration for a multi-statement extraction run."""
    pdf_path: str
    output_dir: str
    model: str = "test-model"
    statements_to_run: Set[StatementType] = field(default_factory=lambda: set(StatementType))
    variants: Dict[StatementType, str] = field(default_factory=dict)
    models: Dict[StatementType, str] = field(default_factory=dict)
    scout_enabled: bool = True
    filing_level: str = "company"


class TestCoordinator:
    """Verify coordinator fans out to sub-agents correctly."""

    @pytest.fixture
    def mock_run_config(self, tmp_path):
        """Config targeting SOFP + SOPL only."""
        return RunConfig(
            pdf_path="/tmp/test.pdf",
            output_dir=str(tmp_path),
            model="test-model",
            statements_to_run={StatementType.SOFP, StatementType.SOPL},
            variants={
                StatementType.SOFP: "CuNonCu",
                StatementType.SOPL: "Function",
            },
        )

    @pytest.fixture
    def mock_infopack(self):
        """Minimal infopack with page refs for SOFP and SOPL."""
        from scout.infopack import Infopack, StatementPageRef
        return Infopack(
            toc_page=2,
            page_offset=2,
            statements={
                StatementType.SOFP: StatementPageRef(
                    variant_suggestion="CuNonCu",
                    face_page=14,
                    note_pages=[30, 31, 32],
                    confidence="HIGH",
                ),
                StatementType.SOPL: StatementPageRef(
                    variant_suggestion="Function",
                    face_page=15,
                    note_pages=[33],
                    confidence="HIGH",
                ),
            },
        )

    @pytest.mark.asyncio
    async def test_coordinator_runs_selected_statements_with_infopack(
        self, mock_run_config, mock_infopack
    ):
        """With infopack + subset, only selected agents run, each scoped to hints."""
        from coordinator import run_extraction

        with patch("coordinator.create_extraction_agent") as mock_factory:
            mock_agent = _make_mock_agent_iter()
            mock_deps = MagicMock()
            mock_deps.filled_path = "/tmp/output/SOFP_filled.xlsx"
            mock_deps.filled_filename = "SOFP_filled.xlsx"
            mock_factory.return_value = (mock_agent, mock_deps)

            result = await run_extraction(mock_run_config, infopack=mock_infopack)

        assert mock_factory.call_count == 2
        call_kwargs_list = [call.kwargs for call in mock_factory.call_args_list]
        for kwargs in call_kwargs_list:
            assert "page_hints" in kwargs
            assert kwargs["page_hints"] is not None

    @pytest.mark.asyncio
    async def test_coordinator_runs_without_infopack(self, mock_run_config):
        """Without infopack, sub-agents get no page hints (full PDF access)."""
        from coordinator import run_extraction

        with patch("coordinator.create_extraction_agent") as mock_factory:
            mock_agent = _make_mock_agent_iter()
            mock_deps = MagicMock()
            mock_deps.filled_path = "/tmp/output/SOFP_filled.xlsx"
            mock_deps.filled_filename = "SOFP_filled.xlsx"
            mock_factory.return_value = (mock_agent, mock_deps)

            result = await run_extraction(mock_run_config, infopack=None)

        assert mock_factory.call_count == 2
        call_kwargs_list = [call.kwargs for call in mock_factory.call_args_list]
        for kwargs in call_kwargs_list:
            assert kwargs.get("page_hints") is None

    @pytest.mark.asyncio
    async def test_coordinator_returns_per_agent_results(self, mock_run_config):
        """Result should contain per-agent success/failure + workbook paths."""
        from coordinator import run_extraction, CoordinatorResult

        with patch("coordinator.create_extraction_agent") as mock_factory:
            mock_agent = _make_mock_agent_iter()
            mock_deps = MagicMock()
            mock_deps.filled_path = "/tmp/output/SOFP_filled.xlsx"
            mock_deps.filled_filename = "SOFP_filled.xlsx"
            mock_deps.statement_type = StatementType.SOFP
            mock_factory.return_value = (mock_agent, mock_deps)

            result = await run_extraction(mock_run_config, infopack=None)

        assert isinstance(result, CoordinatorResult)
        assert len(result.agent_results) == 2

    @pytest.mark.asyncio
    async def test_coordinator_handles_agent_failure(self, mock_run_config):
        """If one agent fails, the others should still complete."""
        from coordinator import run_extraction

        call_count = 0

        def factory_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_deps = MagicMock()
            mock_deps.filled_path = f"/tmp/{call_count}_filled.xlsx"
            mock_deps.filled_filename = f"{call_count}_filled.xlsx"
            mock_deps.statement_type = kwargs["statement_type"]

            if call_count == 1:
                # First agent fails inside iter()
                mock_agent = _make_mock_agent_iter(side_effect=Exception("LLM timeout"))
            else:
                mock_agent = _make_mock_agent_iter()
            return mock_agent, mock_deps

        with patch("coordinator.create_extraction_agent", side_effect=factory_side_effect):
            result = await run_extraction(mock_run_config, infopack=None)

        assert len(result.agent_results) == 2
        statuses = [r.status for r in result.agent_results]
        assert "succeeded" in statuses
        assert "failed" in statuses

    @pytest.mark.asyncio
    async def test_coordinator_all_five_statements(self, tmp_path):
        """Running all 5 statements produces 5 agent results."""
        from coordinator import run_extraction

        config = RunConfig(
            pdf_path="/tmp/test.pdf",
            output_dir=str(tmp_path),
            model="test-model",
            statements_to_run=set(StatementType),
            variants={
                StatementType.SOFP: "CuNonCu",
                StatementType.SOPL: "Function",
                StatementType.SOCI: "BeforeTax",
                StatementType.SOCF: "Indirect",
                StatementType.SOCIE: "Default",
            },
        )

        with patch("coordinator.create_extraction_agent") as mock_factory:
            mock_agent = _make_mock_agent_iter()
            mock_deps = MagicMock()
            mock_deps.filled_path = "/tmp/output/filled.xlsx"
            mock_deps.filled_filename = "filled.xlsx"
            mock_deps.statement_type = StatementType.SOFP
            mock_factory.return_value = (mock_agent, mock_deps)

            result = await run_extraction(config, infopack=None)

        assert mock_factory.call_count == 5
        assert len(result.agent_results) == 5
