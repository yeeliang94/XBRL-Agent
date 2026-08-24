"""Pin the instruction hierarchy at every document-reading LLM stage."""
from pathlib import Path

import pytest

from ingest.pdf_sidecar import TRANSCRIBE_PROMPT
from scout.agent import _SYSTEM_PROMPT
from scout.calibrator import _VALIDATION_PROMPT
from scout.notes_discoverer_vision import _VISION_SYSTEM_PROMPT
from scout.vision import _TOC_EXTRACTION_PROMPT


REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "prompt",
    [
        _SYSTEM_PROMPT,
        _TOC_EXTRACTION_PROMPT,
        _VALIDATION_PROMPT,
        _VISION_SYSTEM_PROMPT,
        TRANSCRIBE_PROMPT,
    ],
)
def test_helper_readers_treat_document_commands_as_data(prompt):
    lowered = prompt.lower()
    assert "not instructions" in lowered


@pytest.mark.parametrize(
    "name",
    [
        "_base.md",
        "_notes_base.md",
        "reviewer.md",
        "notes_reviewer.md",
        "notes_formatter.md",
        "spot_check.md",
    ],
)
def test_main_agents_treat_document_commands_as_data(name):
    lowered = (REPO / "prompts" / name).read_text(encoding="utf-8").lower()
    assert "not instructions" in lowered
