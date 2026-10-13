"""Offline evidence and independent-assessment boundaries."""
from types import SimpleNamespace

import pytest
from pydantic_ai.usage import RunUsage, UsageLimits

from mtool.notes_decorate import DEFAULT_STYLE
from notes import visual_review as vr
from notes.visual_review import prepare_note_evidence as prepare_note_evidence_real


@pytest.fixture
def review_harness(monkeypatch):
    import notes.formatting_agent as fa
    original_factory = vr.create_visual_review_agent
    original_preload = fa._preload_source_pages
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
    return kwargs, decision, SimpleNamespace(calls=calls, factory=original_factory, preload=original_preload)


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
async def test_a_structural_finding_is_never_a_pass(review_harness):
    kwargs, decision, _ = review_harness
    decision[0].update(status="pass", issue_kind="structure", reason="Total row is heading cells.",
                       observed_text="Closing 2024")
    errors, result = await vr.check_visual_rows(**kwargs)
    assert errors[112] == "Table 0: Total row is heading cells."
    assert result["tables"][0]["status"] == "unresolved"
    assert vr.structure_findings({"failed_rows": [112], "visual_review": result}) == {
        112: [(0, "Total row is heading cells.")]}


@pytest.mark.asyncio
async def test_formatter_layout_is_context_beside_the_actual_output(review_harness):
    kwargs, _, harness = review_harness
    kwargs["rows"][112] = kwargs["rows"][112].replace("Closing 2024", "<p>Closing 2024</p><p>At year end</p>")
    layout = {"row": 112, "tables": [{"table": 0, "columns": ["label", "amount"],
                                      "lines": [{"after_row": 1, "from_col": 2, "to_col": 2}]}]}
    await vr.check_visual_rows(**kwargs, layouts={112: layout})
    content = next(part for part in harness.calls[0][0]
                   if isinstance(part, str) and "FORMATTED NOTE ROW 112" in part)
    assert "columns 1=label, 2=amount" in content
    assert "single line below row 1, columns 2-2" in content
    assert '"paragraph": 2, "text": "At year end"' in content
    # The real candidate and prepared output stay the evidence, table by table.
    assert "TABLE 0 CANONICAL CANDIDATE HTML:" in content and "TABLE 0 PREPARED OUTPUT HTML:" in content


@pytest.mark.asyncio
async def test_unquoted_structure_claim_and_content_defect_never_trigger_recopy(review_harness, monkeypatch):
    """Run 183 AMSB row 125: one finding mixed a real missing word with a
    structure defect the checker took from the adjacent comparative table."""
    kwargs, decision, harness = review_harness
    kwargs["rows"][112] = ("<p>Net carrying amount</p>"
                           "<table><tr><td>At 31 December 2025</td></tr><tr><td>Net carrying amount</td><td>1</td></tr></table>"
                           "<table><tr><td>At 31 December 2024</td></tr><tr><td>Net carrying amount</td><td>2</td></tr></table>")
    prepared = kwargs["rows"][112]
    monkeypatch.setattr(vr, "prepare_note_evidence", lambda html, style: (prepared, {"html_sha256": "abc"}))
    decision[:] = [
        {"row": 112, "table": 0, "status": "unresolved", "source_pages": [3], "issue_kind": "structure",
         "reason": "Dated row labelled 2025 should be a heading.", "observed_text": "At 31 December 2023"},
        {"row": 112, "table": 1, "status": "unresolved", "source_pages": [3], "issue_kind": "structure",
         "reason": "Heading split from figures.", "observed_text": "Net carrying amount",
         "content_defect": "Asset header lacks 'land'"},
    ]
    errors, result = await vr.check_visual_rows(**kwargs)
    content = next(part for part in harness.calls[0][0]
                   if isinstance(part, str) and "FORMATTED NOTE ROW 112" in part)
    assert "TABLE 1 — preceding text: Net carrying amount; first row: At 31 December 2024" in content
    assert "did not quote text from this table" in errors[112]
    assert "Asset header lacks 'land'" in errors[112]
    assert result["tables"][1]["issue_kind"] == "source_content"
    assert vr.structure_findings({"failed_rows": [112], "visual_review": result}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["source_content", "source_provenance", "source_emphasis", "unsupported_layout"])
@pytest.mark.parametrize("status", ["needs_correction", "pass"])
async def test_source_defect_never_enters_style_correction(review_harness, kind, status):
    kwargs, decision, _ = review_harness
    decision[0].update(issue_kind=kind, status=status, reason="Recorded layout cannot represent this table.",
                       observed_text="Closing 2024")
    async def formatter_run(prompt):
        raise AssertionError("Source defects cannot be repaired by style changes")
    errors, review, corrected = await vr.review_and_correct_rows(
        rows=kwargs["rows"], cells=kwargs["cells"], style=kwargs["style"], run_id=1,
        db_path="unused", pdf_path="unused", sheet=kwargs["sheet"], model="unused",
        usage=RunUsage(), formatter_run=formatter_run, output_dir="", trace_label="test",
    )
    assert ("cannot produce" if kind == "unsupported_layout" else "human review") in errors[112]
    assert corrected == {}
    assert review["tables"][0]["issue_kind"] == kind
    assert review["tables"][0]["status"] == "unresolved"
    # Source defects go to human review, not to the structural re-copy.
    assert vr.structure_findings({"failed_rows": [112], "visual_review": review}) == {}
    # Only a formatter limit lets the note keep its best-effort formatting.
    assert review["limitation_rows"] == ([112] if kind == "unsupported_layout" else [])


@pytest.mark.asyncio
async def test_unquoted_unsupported_claim_gets_an_ordinary_correction(review_harness):
    # "Cannot be drawn" is not evidence unless it quotes the table; otherwise
    # it would let the checker skip correction and fail a fixable note.
    kwargs, decision, _ = review_harness
    decision[0].update(issue_kind="unsupported_layout", status="unresolved",
                       reason="Inset rule cannot be drawn.", observed_text="Not in this table")
    errors, result = await vr.check_visual_rows(**kwargs)
    assert result["tables"][0]["issue_kind"] == "style"
    assert result["tables"][0]["status"] == "needs_correction"
    assert result["limitation_rows"] == []
    assert "Inset rule cannot be drawn." in errors[112]


@pytest.mark.asyncio
async def test_limit_only_row_keeps_corrected_html_after_recheck(review_harness, monkeypatch):
    # A style finding is corrected; the recheck leaves only a formatter limit,
    # so the corrected candidate is returned instead of discarded.
    kwargs, decision, _ = review_harness
    decision[0].update(issue_kind="style", status="needs_correction", reason="Amounts should be right-aligned.",
                       observed_text="Closing 2024")
    checks = iter([
        ({112: "Table 0: Amounts should be right-aligned."},
         {"tables": [dict(decision[0])], "limitation_rows": []}),
        ({112: "Table 0: needs a layout the formatter cannot produce: inset rule"},
         {"tables": [], "limitation_rows": [112]}),
    ])

    async def fake_check(**_):
        return next(checks)

    monkeypatch.setattr(vr, "check_visual_rows", fake_check)
    corrected_html = "<table><tr><td>Closing 2024</td><td style=\"text-align: right\">1</td></tr></table>"

    async def formatter_run(prompt):
        return SimpleNamespace(output={})

    import notes.formatting_agent as fa
    monkeypatch.setattr(fa, "_screen_patch", lambda output, sheet, revised=False: (
        None, SimpleNamespace(patch={"notes": []}), None))
    monkeypatch.setattr(fa, "_partition_layout", lambda base, patch, padding: (
        {"cells": [{"row": 112, "html": corrected_html}]}, None, {}))
    monkeypatch.setattr(vr, "apply_sheet_patch", lambda base, patch: SimpleNamespace(rows={112: corrected_html}))
    errors, review, corrected = await vr.review_and_correct_rows(
        rows=kwargs["rows"], cells=kwargs["cells"], style=kwargs["style"], run_id=1,
        db_path="unused", pdf_path="unused", sheet=kwargs["sheet"], model="unused",
        usage=RunUsage(), formatter_run=formatter_run, output_dir="", trace_label="test",
    )
    assert 112 in errors
    assert review["limitation_rows"] == [112]
    assert corrected == {112: corrected_html}


@pytest.mark.asyncio
async def test_review_can_distinguish_double_intent_after_transport_normalization(review_harness, monkeypatch):
    from bs4 import BeautifulSoup

    kwargs, _, harness = review_harness
    monkeypatch.setattr(vr, "prepare_note_evidence", prepare_note_evidence_real)
    prepared_edges = []
    for border in ("3px double", "0.75px solid"):
        html = ('<table><tr><td>Total</td><td style="border-bottom:'
                + border + ' #000000">24,000</td></tr></table>')
        kwargs["rows"][112] = html
        errors, _ = await vr.check_visual_rows(**kwargs)
        assert errors == {}
        note = next(part for part in harness.calls[-1][0]
                    if isinstance(part, str) and "PREPARED OUTPUT HTML:" in part)
        canonical, prepared = note.split("\nTABLE 0 PREPARED OUTPUT HTML:\n")
        assert "TABLE 0 CANONICAL CANDIDATE HTML:\n" + html in canonical
        assert "double" not in prepared
        amount = BeautifulSoup(prepared, "html.parser").find_all("td")[1]
        styles = dict(decl.strip().split(":", 1) for decl in amount["style"].split(";") if decl.strip())
        styles = {prop: value.strip() for prop, value in styles.items()}
        assert styles["border-bottom"] == "0.75px solid #000000"
        assert amount.get_text() == "24,000"
        prepared_edges.append(styles)
    # The same transport output must not hide whether the source double rule
    # was actually captured. The reviewer needs both current representations.
    assert prepared_edges[0] == prepared_edges[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "duplicate", "unexpected", "unseen", "unresolved", "missing_source", "foreign_note", "mixed_notes"])
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
    elif defect in {"foreign_note", "mixed_notes"}:
        kwargs["rows"][113] = kwargs["rows"][112]
        kwargs["cells"].append(SimpleNamespace(row=113, source_pages=[4]))
        decision.append({"row": 113, "table": 0, "status": "pass", "source_pages": [4],
                         "reason": "The other note was checked on page 4."})
        decision[0]["source_pages"] = [4] if defect == "foreign_note" else [3, 4]
    else:
        decision[0]["status"] = "unresolved"
    errors, _ = await vr.check_visual_rows(**kwargs)
    assert 112 in errors
    if defect in {"foreign_note", "mixed_notes"}:
        assert 113 not in errors


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_page", [False, True])
async def test_tables_on_different_pages_use_their_own_source_citations(review_harness, shared_page):
    kwargs, decision, _ = review_harness
    kwargs["rows"][112] += "<table><tr><td>Comparative</td><td>2</td></tr></table>"
    kwargs["cells"][0].source_pages = [3, 4]
    decision.append({"row": 112, "table": 1, "status": "pass", "source_pages": [4],
                     "reason": "Second table checked on its source page."})
    if shared_page:
        kwargs["rows"][113] = kwargs["rows"][112]
        kwargs["cells"].append(SimpleNamespace(row=113, source_pages=[4]))
        decision.extend([
            {"row": 113, "table": table, "status": "pass", "source_pages": [4],
             "reason": "This note shares page 4 with the preceding note."}
            for table in (0, 1)
        ])
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
@pytest.mark.parametrize("scenario", ["all_missing", "other_note", "continuation"])
async def test_missing_source_images_only_block_affected_notes(review_harness, monkeypatch, scenario):
    import notes.formatting_agent as fa
    import notes.agent as source_agent
    kwargs, decision, harness = review_harness
    calls = harness.calls
    if scenario != "all_missing":
        kwargs["rows"][113] = kwargs["rows"][112]
        kwargs["cells"].append(SimpleNamespace(row=113, source_pages=[4] if scenario == "other_note" else [3]))
        if scenario == "continuation":
            kwargs["cells"][0].source_pages = [3, 4]
            decision[0]["row"] = 113

    async def render(pdf_path, page, dpi, **kwargs):
        if scenario == "all_missing" or page == 4:
            raise ValueError("Source page cannot be rendered")
        return b"source image"
    monkeypatch.setattr(source_agent, "_render_one_page_single_flight", render)
    monkeypatch.setattr(fa, "_preload_source_pages", harness.preload)
    errors, _ = await vr.check_visual_rows(**kwargs)
    if scenario == "all_missing":
        assert 112 in errors and calls == []
    else:
        unresolved = 113 if scenario == "other_note" else 112
        reviewed = 112 if scenario == "other_note" else 113
        assert set(errors) == {unresolved}
        assert len(calls) == 1
        prompt = calls[0][0]
        assert any(f"FORMATTED NOTE ROW {reviewed}" in part for part in prompt if isinstance(part, str))
        assert not any(f"FORMATTED NOTE ROW {unresolved}" in part for part in prompt if isinstance(part, str))


def test_evidence_is_the_prepared_copy_output():
    from mtool.notes_exporter import prepare_note_output
    html = "<table><tr><td>Closing</td><td>1</td></tr></table>"
    prepared, identity = vr.prepare_note_evidence(html, DEFAULT_STYLE)
    assert prepared == prepare_note_output(html, style=DEFAULT_STYLE, decorate=True)["html"]
    assert len(identity["html_sha256"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [[], [3, 4], [9]])
async def test_rejected_citation_never_reaches_recopy(review_harness, pages):
    """Review finding: an unseen-page structure finding quoting real table text
    still reached structure_findings."""
    kwargs, decision, _ = review_harness
    decision[0].update(status="unresolved", issue_kind="structure", source_pages=pages,
                       reason="Total row is heading cells.", observed_text="Closing 2024")
    errors, result = await vr.check_visual_rows(**kwargs)
    assert 112 in errors
    assert result["tables"][0]["status"] == "unresolved"
    assert vr.structure_findings({"failed_rows": [112], "visual_review": result}) == {}


@pytest.mark.asyncio
async def test_duplicate_assessment_never_reaches_recopy(review_harness):
    """Greptile PR #34: a duplicate structure assessment skipped the evidence
    checks and, as the last entry for its table, still requested a re-copy."""
    kwargs, decision, _ = review_harness
    decision.append({"row": 112, "table": 0, "status": "unresolved", "issue_kind": "structure",
                     "source_pages": [], "reason": "Total row is heading cells."})
    errors, result = await vr.check_visual_rows(**kwargs)
    assert "duplicate" in errors[112]
    assert vr.structure_findings({"failed_rows": [112], "visual_review": result}) == {}
