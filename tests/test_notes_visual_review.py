"""Offline evidence and independent-assessment boundaries."""
from types import SimpleNamespace

import pytest
from pydantic_ai.usage import RunUsage, UsageLimits

from mtool.notes_decorate import DEFAULT_STYLE
from notes import visual_review as vr


@pytest.fixture
def review_harness(monkeypatch):
    import notes.formatting_agent as fa
    original_factory = vr.create_visual_review_agent
    calls = []
    def prepare(html, style):
        return "<table><tr><td>prepared</td></tr></table>", {"html_sha256": "abc"}
    async def source(pdf_path, pages):
        return ["source images"], set(pages)
    monkeypatch.setattr(vr, "prepare_note_evidence", prepare)
    monkeypatch.setattr(fa, "_preload_source_pages", source)
    decision = [{"row": 112, "table": 0, "status": "pass", "source_pages": [3], "reason": "Both years checked."}]
    class Agent:
        async def run(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            assert "message_history" not in kwargs
            return SimpleNamespace(output={"tables": decision}, all_messages=lambda: [])
    def factory(**kwargs):
        return Agent(), SimpleNamespace(viewed_pages=set())
    monkeypatch.setattr(vr, "create_visual_review_agent", factory)
    kwargs = dict(rows={112: "<table><tr><td>Closing 2024</td><td>1</td></tr></table>"},
                  cells=[SimpleNamespace(row=112, source_pages=[3])], style=DEFAULT_STYLE,
                  run_id=1, db_path="unused", pdf_path="unused", sheet="Notes-Listofnotes",
                  model="unused", usage=RunUsage(), limits=UsageLimits(request_limit=16),
                  output_dir="", trace_label="test")
    return kwargs, decision, SimpleNamespace(calls=calls, factory=original_factory)


@pytest.mark.asyncio
async def test_prepared_output_is_given_to_fresh_reviewer(review_harness):
    kwargs, _, harness = review_harness
    calls = harness.calls
    errors, result = await vr.check_visual_rows(**kwargs)
    assert errors == {} and result["evidence"][112]["html_sha256"] == "abc"
    assert any("PREPARED OUTPUT HTML:\n<table><tr><td>prepared</td></tr></table>" in part
               for part in calls[0][0] if isinstance(part, str))
    assert calls[0][1]["usage"] is kwargs["usage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "duplicate", "unexpected", "unseen", "unresolved", "missing_source"])
async def test_incomplete_or_ambiguous_assessment_never_passes(review_harness, defect):
    kwargs, decision, _ = review_harness
    if defect == "missing":
        decision.clear()
    elif defect == "duplicate":
        decision.append(dict(decision[0]))
    elif defect == "unexpected":
        decision[0]["table"] = 9
    elif defect == "unseen":
        decision[0]["source_pages"] = [3, 4]
    elif defect == "missing_source":
        decision[0]["source_pages"] = []
    else:
        decision[0]["status"] = "unresolved"
    errors, _ = await vr.check_visual_rows(**kwargs)
    assert 112 in errors


@pytest.mark.asyncio
async def test_tables_on_different_pages_use_their_own_source_citations(review_harness):
    kwargs, decision, _ = review_harness
    kwargs["rows"][112] += "<table><tr><td>Comparative</td><td>2</td></tr></table>"
    kwargs["cells"][0].source_pages = [3, 4]
    decision.append({"row": 112, "table": 1, "status": "pass", "source_pages": [4],
                     "reason": "Second table checked on its source page."})
    errors, _ = await vr.check_visual_rows(**kwargs)
    assert errors == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [None, "duplicate", "missing"])
async def test_all_table_findings_for_one_note_reach_the_correction(review_harness, defect):
    kwargs, decision, _ = review_harness
    kwargs["rows"][112] += "<table><tr><td>Comparative</td><td>2</td></tr></table>"
    decision[0].update(status="needs_correction", reason="Wrong Addition upper edge.")
    decision.append({"row": 112, "table": 1, "status": "needs_correction", "source_pages": [3],
                     "reason": "Missing comparative closing lower edge."})
    if defect == "duplicate":
        decision.append(dict(decision[0]))
    elif defect == "missing":
        decision.pop()
    errors, _ = await vr.check_visual_rows(**kwargs)
    assert "Addition" in errors[112]
    if defect == "missing":
        assert "omitted" in errors[112]
    else:
        assert "comparative" in errors[112]
    if defect == "duplicate":
        assert "duplicate" in errors[112]


@pytest.mark.asyncio
async def test_unpreparable_output_does_not_call_model(review_harness, monkeypatch):
    kwargs, _, harness = review_harness
    calls = harness.calls
    def oversize(*args):
        raise ValueError("Prepared note exceeds the output size limit.")
    monkeypatch.setattr(vr, "prepare_note_evidence", oversize)
    errors, result = await vr.check_visual_rows(**kwargs)
    assert errors[112] == "Prepared note exceeds the output size limit." and calls == []
    assert result["tables"] == []


@pytest.mark.asyncio
async def test_exhausted_review_budget_returns_unresolved_assessment(review_harness, monkeypatch):
    from pydantic_ai.models.test import TestModel
    kwargs, decision, harness = review_harness
    monkeypatch.setattr(vr, "create_visual_review_agent", harness.factory)
    kwargs["model"] = TestModel(custom_output_args={"tables": decision})
    kwargs["usage"] = RunUsage(requests=1)
    kwargs["limits"] = UsageLimits(request_limit=1)
    errors, review = await vr.check_visual_rows(**kwargs)
    assert review["error_type"] == "turn_budget"
    assert "request budget" in errors[112]


@pytest.mark.asyncio
async def test_missing_source_images_never_reaches_the_reviewer(review_harness, monkeypatch):
    import notes.formatting_agent as fa
    kwargs, _, harness = review_harness
    calls = harness.calls
    async def missing(pdf_path, pages):
        return [], set()
    monkeypatch.setattr(fa, "_preload_source_pages", missing)
    errors, _ = await vr.check_visual_rows(**kwargs)
    assert 112 in errors and calls == []


def test_evidence_is_the_prepared_copy_output():
    from mtool.notes_exporter import prepare_note_output
    html = "<table><tr><td>Closing</td><td>1</td></tr></table>"
    prepared, identity = vr.prepare_note_evidence(html, DEFAULT_STYLE)
    assert prepared == prepare_note_output(html, style=DEFAULT_STYLE, decorate=True)["html"]
    assert len(identity["html_sha256"]) == 64
