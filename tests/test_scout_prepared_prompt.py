"""Scout prompt assembly keeps the selected inventory workflow explicit."""
from types import SimpleNamespace

import fitz
import pytest
from pydantic_ai.models.test import TestModel

import scout.agent as scout


@pytest.mark.parametrize("prepared", [False, True])
def test_factory_uses_matching_inventory_instructions(tmp_path, monkeypatch, prepared):
    path = tmp_path / "uploaded.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(path)
    monkeypatch.setattr(scout, "_prepared_for_scout",
                        lambda _: SimpleNamespace(revision="r1") if prepared else None)
    agent, deps = scout.create_scout_agent(path, model=TestModel())
    prompt = agent._system_prompts[0]
    assert "{notes_inventory_" not in prompt
    if prepared:
        assert "Read all notes pages with read_prepared_source" in prompt
        assert "Never skip `discover_notes_inventory`" not in prompt
        assert "This step is\n   mandatory" not in prompt
        assert "PDF pages remain available" in prompt
        assert deps.prepared_revision == "r1"
    else:
        assert "Never skip `discover_notes_inventory`" in prompt
        assert "Checked document preparation is available" not in prompt
        assert deps.prepared_revision is None
