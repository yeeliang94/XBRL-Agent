"""Offline preparation contracts: no paid provider calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import fitz
import pytest

from ingest.document_preparation import (
    PreparationError, RequestBudget, prepare_document, read_prepared_document,
)


@pytest.fixture(autouse=True)
def single_page_requests(monkeypatch):
    """Exercise existing per-page repair contracts independently of coalescing.

    Default two-page transport, fallback and cancellation are exercised end to
    end in test_document_preparation_batching.py using the real batcher.
    """
    async def direct(self, stage, images, context):
        return await self.caller(stage, images, context)
    monkeypatch.setattr("ingest.document_preparation._PageRequestBatcher.request", direct)


def pdf(tmp_path, pages=2):
    path = tmp_path / "uploaded.pdf"
    with fitz.open() as doc:
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Source page {i + 1}")
        doc.save(path)
    return path


def caller(*, fail_verify=False, rotation=0, link=False):
    calls = []
    async def call(stage, images, context):
        calls.append((stage, context))
        result = {"complete": True, "readable": True, "verified": True,
                  "rotation": rotation, "links": []}
        if stage == "capturing":
            result["html"] = f'<h2>Note</h2><p><strong>Text {context["page"]}</strong></p>'
        if stage == "verifying" and fail_verify:
            result["verified"] = False
        if stage == "joining" and link:
            result["links"] = [{"from_block_id": context["previous_blocks"][-1]["block_id"],
                                "to_block_id": context["next_blocks"][-1]["block_id"], "separator": " "}]
        return result
    return call, calls


def test_complete_capture_preserves_emphasis_and_links_and_reuses(tmp_path):
    path = pdf(tmp_path)
    original = path.read_bytes()
    call, calls = caller(link=True, rotation=90)
    events = []
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call, on_progress=events.append))
    assert '<strong>Text 1</strong>' in result.source_html_path.read_text()
    assert result.blocks[-1]["continues_block_id"] == result.blocks[1]["block_id"]
    assert result.blocks[1]["locator"]["heading_ancestor_ids"] == [result.blocks[0]["block_id"]]
    assert path.read_bytes() == original
    with fitz.open(result.prepared_pdf_path) as doc:
        assert doc[0].rotation == 90
    before = len(calls)
    assert asyncio.run(prepare_document(path, None, model_name="fake", _caller=call)).revision == result.revision
    assert len(calls) == before
    assert events[-1]["stage"] == "succeeded"
    assert events[-1]["verified"] == 2
    assert read_prepared_document(path, model_name="different") is None


def test_unconfirmed_reading_continues_with_honest_uncertainty(tmp_path):
    path = pdf(tmp_path, 1)
    (tmp_path / "source.html").write_text("previous valid source")
    call, calls = caller(fail_verify=True)
    events = []
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call, on_progress=events.append))
    assert result.pages[0]["verified"] is False
    assert result.pages[0]["assessment_complete"] is True
    assert result.pages[0]["capture_status"] == "best_effort"
    assert result.pages[0]["uncertainties"]
    assert all(b["locator"]["capture_uncertain"] for b in result.blocks)
    assert read_prepared_document(path) is not None
    meta = json.loads((tmp_path / "source_meta.json").read_text())
    assert meta["verified"] is False
    assert events[-1]["checked"] == 1
    assert events[-1]["verified"] == 0
    assert len([c for c in calls if c[0] == "verifying"]) == 2


@pytest.mark.parametrize("readable", [True, False])
def test_truncation_receipt_rejected(tmp_path, readable):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def incomplete(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["complete"] = False
            result["readable"] = readable
            result["uncertainties"] = [{"reason": "Capture did not assess the entire page"}]
        return result
    with pytest.raises(PreparationError, match="preparation did not finish"):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=incomplete))
    assert not (tmp_path / "preparation.json").exists()


def test_verification_timeout_retries_only_failed_request(tmp_path):
    path = pdf(tmp_path, 1)
    base, calls = caller()
    attempts = 0
    async def transient(stage, images, context):
        nonlocal attempts
        if stage == 'verifying':
            attempts += 1
            if attempts == 1:
                raise TimeoutError('temporary request timeout')
        return await base(stage, images, context)
    events = []
    result = asyncio.run(prepare_document(path, None, model_name='fake', _caller=transient,
                                         on_progress=events.append))
    assert result.pages[0]['verified'] is True
    assert attempts == 2
    assert sum(stage == 'capturing' for stage, _ in calls) == 1
    assert any('Retrying' in event['message'] for event in events)


def test_uncertain_join_preserves_both_blocks_without_blocking_draft(tmp_path):
    path = pdf(tmp_path)
    base, _ = caller(link=True)
    async def bad_link(stage, images, context):
        result = await base(stage, images, context)
        if stage == 'joining':
            result['links'][0]['to_block_id'] = 'stale-block'
        return result
    result = asyncio.run(prepare_document(path, None, model_name='fake', _caller=bad_link))
    assert len(result.blocks) == 4
    assert all(block['continues_block_id'] is None for block in result.blocks)
    assert all(page['capture_status'] == 'best_effort' for page in result.pages)
    assert 'Text 1' in result.source_html_path.read_text()
    assert 'Text 2' in result.source_html_path.read_text()


def test_verified_page_checkpoint_resumes_after_failed_boundary(tmp_path):
    path = pdf(tmp_path)
    base, calls = caller()
    async def broken(stage, images, context):
        result = await base(stage, images, context)
        if stage == "joining":
            raise RuntimeError("provider unavailable")
        return result
    with pytest.raises(RuntimeError):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=broken))
    capture_count = sum(c[0] == "capturing" for c in calls)
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assert sum(c[0] == "capturing" for c in calls) == capture_count


def test_metadata_rotation_not_applied_twice(tmp_path):
    path = pdf(tmp_path, 1)
    with fitz.open(path) as doc:
        doc[0].set_rotation(90)
        doc.saveIncr()
    call, _ = caller(rotation=0)
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    with fitz.open(result.prepared_pdf_path) as doc:
        assert doc[0].rotation == 90
    assert result.pages[0]["metadata_rotation"] == 90


def test_repeat_copies_complete_preparation_for_reuse_and_inventory_remap(tmp_path):
    import server
    path = pdf(tmp_path, 1)
    call, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    state = {"status": "succeeded", "model_name": "fake", "infopack": {"notes_inventory": []}}
    (tmp_path / "preparation_status.json").write_text(json.dumps(state))
    repeat = server._seed_repeat_session_dir(tmp_path, 1)
    copied = read_prepared_document(repeat / "uploaded.pdf", model_name="fake")
    assert copied is not None
    assert copied.revision == prepared.revision
    assert copied.prepared_pdf_path.parent == repeat
    assert json.loads((repeat / "preparation_status.json").read_text()) == state
    checkpoint = next(tmp_path.glob("preparation-checkpoint-*.json"))
    assert (repeat / checkpoint.name).read_bytes() == checkpoint.read_bytes()


def test_repeated_reads_reuse_hashes_but_same_size_edits_invalidate(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    path = pdf(tmp_path, 1)
    call, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    opened = []
    original_open = Path.open
    def tracked_open(self, mode="r", *args, **kwargs):
        if mode == "rb":
            opened.append(self)
        return original_open(self, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracked_open)
    assert read_prepared_document(path) is not None
    initial_reads = len(opened)
    assert read_prepared_document(path) is not None
    assert read_prepared_document(path) is not None
    assert len(opened) == initial_reads
    stat = prepared.source_html_path.stat()
    html = prepared.source_html_path.read_text()
    assert "Text" in html
    prepared.source_html_path.write_text(html.replace("Text", "Lost"))
    os.utime(prepared.source_html_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert read_prepared_document(path) is None


def test_checkpoint_disk_writes_do_not_run_on_preparation_event_loop(tmp_path, monkeypatch):
    import ingest.document_preparation as preparation
    original_write = preparation._atomic_text
    main_thread = threading.get_ident()
    checkpoint_threads = []
    def tracked_write(path, content):
        if path.name.startswith("preparation-checkpoint-"):
            checkpoint_threads.append(threading.get_ident())
        original_write(path, content)
    monkeypatch.setattr(preparation, "_atomic_text", tracked_write)
    path = pdf(tmp_path, 3)
    call, calls = caller(link=True)
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    assert checkpoint_threads and main_thread not in checkpoint_threads
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert len(checkpoint["calls"]) == len(calls)
    assert set(checkpoint["pages"]) == {"1", "2", "3"}


def test_preparation_artifacts_use_resilient_atomic_replace(tmp_path, monkeypatch):
    import ingest.document_preparation as preparation

    calls = []

    def replace(source, destination):
        calls.append((Path(source), Path(destination)))
        Path(source).replace(destination)

    monkeypatch.setattr(preparation, "replace_with_retry", replace)
    metadata = tmp_path / "preparation.json"
    preparation._atomic_text(metadata, "{}")

    source = pdf(tmp_path, 1)
    prepared = tmp_path / "prepared.pdf"
    preparation._derived_pdf(source, prepared, [{"page": 1, "metadata_rotation": 0, "rotation": 0}])

    assert [destination for _, destination in calls] == [metadata, prepared]
    assert metadata.read_text(encoding="utf-8") == "{}"
    with fitz.open(prepared) as document:
        assert len(document) == 1


def test_concurrent_checkpoint_saves_do_not_rewrite_identical_snapshots(tmp_path, monkeypatch):
    from itertools import groupby
    import time
    import ingest.document_preparation as preparation

    original_write = preparation._atomic_text
    snapshots = []
    def slow_write(path, content):
        if path.name.startswith("preparation-checkpoint-"):
            time.sleep(0.01)  # Let other page results queue behind this write.
            snapshots.append(content)
        original_write(path, content)
    monkeypatch.setattr(preparation, "_atomic_text", slow_write)
    path = pdf(tmp_path, 10)
    call, calls = caller()
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    unique_snapshots = [content for content, _ in groupby(snapshots)]
    assert len(snapshots) == len(unique_snapshots)
    final = json.loads(snapshots[-1])
    assert len(final["calls"]) == len(calls)
    assert len(final["pages"]) == 10


@pytest.mark.asyncio
async def test_cancel_waits_for_checkpoint_write_before_allowing_retry(tmp_path, monkeypatch):
    import ingest.document_preparation as preparation
    original_write = preparation._atomic_text
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def slow_write(path, content):
        if path.name.startswith("preparation-checkpoint-"):
            started.set()
            assert release.wait(5), "Test did not release checkpoint write"
            original_write(path, content)
            finished.set()
        else:
            original_write(path, content)
    monkeypatch.setattr(preparation, "_atomic_text", slow_write)
    path = pdf(tmp_path, 1)
    call, _ = caller()
    task = asyncio.create_task(prepare_document(path, None, model_name="fake", _caller=call))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel("stop")
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert not (tmp_path / "preparation.json").exists()
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert checkpoint["calls"]


def test_budget_cross_loop_and_cancelled_waiter_do_not_leak():
    budget = RequestBudget(2)
    lock = threading.Lock()
    active = peak = 0
    async def work():
        nonlocal active, peak
        async with budget.slot():
            with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            with lock:
                active -= 1
    def run():
        async def batch():
            await asyncio.gather(*(work() for _ in range(4)))
        asyncio.run(batch())
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result()
    assert peak == 2
    assert budget._active == 0
    async def cancellation():
        single = RequestBudget(1)
        async with single.slot():
            waiter = asyncio.create_task(work_with(single))
            await asyncio.sleep(0.02)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert not single._waiting
        assert single._active == 0
    async def work_with(single):
        async with single.slot():
            pass
    asyncio.run(cancellation())


def test_cancel_preserves_verified_pages_without_activation(tmp_path, monkeypatch):
    import ingest.document_preparation as preparation
    saved = threading.Event()
    atomic_text = preparation._atomic_text
    def observe_save(path, content):
        atomic_text(path, content)
        if path.name.startswith("preparation-checkpoint-"):
            if json.loads(content).get("pages", {}).get("1", {}).get("verified"):
                saved.set()
    monkeypatch.setattr(preparation, "_atomic_text", observe_save)
    path = pdf(tmp_path, 2)
    base, _ = caller()
    async def cancelled(stage, images, context):
        if context.get("page") == 2:
            # A request slot no longer implies a single page worker. Cancel only
            # after page 1 is durably saved, which is the contract under test.
            assert await asyncio.to_thread(saved.wait, 3)
            raise asyncio.CancelledError()
        return await base(stage, images, context)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(prepare_document(path, None, model_name="fake", concurrency=2, _caller=cancelled))
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert checkpoint["pages"]["1"]["verified"] is True
    assert not (tmp_path / "preparation.json").exists()


def test_reconcile_assigns_every_block_and_rejects_missing_owner(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path, 1)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assignments = [{"block_id": b["block_id"], "owner_kind": "note",
        "source_note_id": "n2", "source_note_num": "2", "source_note_title": "Policies"}
        for b in prepared.blocks]
    result = asyncio.run(reconcile_prepared_inventory(prepared, assignments=assignments))
    assert all(b["source_note_id"] == "n2" for b in result.blocks)
    assert result.blocks[0]["locator"]["source_note_num"] == "2"
    before = result.metadata_path.read_bytes()
    for invalid in (assignments[:-1], [assignments[0], assignments[0]]):
        with pytest.raises(PreparationError, match="exactly once"):
            asyncio.run(reconcile_prepared_inventory(result, assignments=invalid))
    assignments[0]["owner_kind"] = "unresolved"
    with pytest.raises(PreparationError, match="remains unresolved"):
        asyncio.run(reconcile_prepared_inventory(result, assignments=assignments))
    assert result.metadata_path.read_bytes() == before


def test_docx_conversion_missing_native_content_cannot_pass(tmp_path, monkeypatch):
    path = pdf(tmp_path, 1)
    (tmp_path / "uploaded.docx").write_bytes(b"native-source-fixture")
    monkeypatch.setattr("ingest.docx_html.extract_docx_html", lambda _: "<p>Missing native paragraph.</p>")
    base, _ = caller()
    with pytest.raises(PreparationError, match="original Word content"):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assert not (tmp_path / "preparation.json").exists()


def test_docx_structure_uncertainty_is_retained_without_blocking(tmp_path, monkeypatch):
    path = pdf(tmp_path, 1)
    (tmp_path / "uploaded.docx").write_bytes(b"native-source-fixture")
    monkeypatch.setattr("ingest.docx_html.extract_docx_html", lambda _: "<h2>Note</h2><p><em>Text 1</em></p>")
    base, _ = caller()
    async def native_check(stage, images, context):
        result = await base(stage, images, context)
        if stage == "native_verifying":
            result["verified"] = False
        return result
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=native_check))
    metadata = json.loads(result.metadata_path.read_text())
    assert metadata["native_structure_verified"] is False
    assert metadata["native_assessment_complete"] is True
    assert metadata["native_uncertainties"]
    assert read_prepared_document(path) is not None


def test_worker_refills_before_slowest_page_finishes_and_output_stays_ordered(tmp_path):
    path = pdf(tmp_path, 4)
    async def run():
        third_started = asyncio.Event()
        base, _ = caller()
        active = peak = 0
        async def delayed(stage, images, context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                if stage == "capturing" and context["page"] == 3:
                    third_started.set()
                if stage == "capturing" and context["page"] == 1:
                    await asyncio.wait_for(third_started.wait(), 2)
                await asyncio.sleep(0.005)
                return await base(stage, images, context)
            finally:
                active -= 1
        result = await prepare_document(path, None, model_name="fake", concurrency=2,
                                        _budget=RequestBudget(2), _caller=delayed)
        assert peak <= 2
        assert [p["page"] for p in result.pages] == [1, 2, 3, 4]
        assert third_started.is_set()
    asyncio.run(run())


def test_boundary_repairs_with_more_context(tmp_path):
    path = pdf(tmp_path)
    base, _ = caller()
    attempts = 0
    async def repair(stage, images, context):
        nonlocal attempts
        result = await base(stage, images, context)
        if stage == "joining":
            attempts += 1
            result["verified"] = attempts > 1
            if attempts > 1:
                assert "earlier_blocks" in context
        return result
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=repair))
    assert attempts == 2


def test_failure_calls_are_persisted_without_confidential_error_text(tmp_path):
    path = pdf(tmp_path, 1)
    async def fails(stage, images, context):
        raise RuntimeError("confidential source details")
    with pytest.raises(RuntimeError):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=fails))
    raw = next(tmp_path.glob("preparation-checkpoint-*.json")).read_text()
    data = json.loads(raw)
    assert data["calls"][0]["status"] == "failed"
    assert data["calls"][0]["error_type"] == "RuntimeError"
    assert "duration_seconds" in data["calls"][0]
    assert "confidential source details" not in raw


def test_source_capture_preserves_ordered_list_numbering_and_rejects_active_html():
    from ingest.pdf_sidecar import normalize_transcription
    html = '<ol start="3" type="a"><li value="5"><u>Policy</u></li></ol>'
    assert normalize_transcription(html, preserve_meaningful_formatting=True) == html
    with pytest.raises(ValueError, match="semantic HTML"):
        normalize_transcription('<p>Text</p><script>alert(1)</script>', preserve_meaningful_formatting=True)


def test_uncertain_margin_gets_focused_capture_and_independent_verification(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    captures = 0
    async def inspect_noise(stage, images, context):
        nonlocal captures
        result = await base(stage, images, context)
        if stage == "capturing":
            captures += 1
            if captures == 1:
                result.update(complete=False, issues=["Uncertain upper margin marks"])
            else:
                assert len(images) == 5
                assert "Uncertain upper margin marks" in context["issues"]
                result["non_text_regions"] = [{"reason": "scanner_noise", "bbox": [0, 0, .1, .1]}]
        if stage == "verifying":
            assert len(images) == 5
            assert context["candidate_non_text_regions"]
        return result
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=inspect_noise))
    assert captures == 2
    assert result.pages[0]["non_text_regions"][0]["reason"] == "scanner_noise"


def test_faint_text_cannot_be_certified_as_noise_without_verifier_agreement(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def disagree(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["non_text_regions"] = [{"reason": "scanner_noise", "bbox": [0, 0, .1, .1]}]
        if stage == "verifying":
            result.update(verified=False, issues=["The excluded region contains a footnote."])
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=disagree))
    assert prepared.pages[0]["verified"] is False
    assert "footnote" in prepared.blocks[0]["locator"]["uncertainty_reason"]


def test_scout_reads_verified_pages_with_explicit_pagination_and_stale_guard(tmp_path):
    from scout.agent import ScoutDeps, _read_prepared_source_impl, _prepared_for_scout
    path = pdf(tmp_path, 2)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    deps = ScoutDeps(prepared.prepared_pdf_path, 2, None, None,
                     prepared_revision=prepared.revision)
    assert _prepared_for_scout(prepared.prepared_pdf_path).revision == prepared.revision
    first = _read_prepared_source_impl(deps, 1, 2, max_blocks=1)
    assert first["complete"] is False
    assert first["next_block_offset"] == 1
    assert len(first["blocks"]) == 1
    final = _read_prepared_source_impl(deps, 1, 2, block_offset=1)
    assert final["complete"] is True
    assert final["next_block_offset"] is None
    assert "error" in _read_prepared_source_impl(deps, 0, 2)
    prepared.source_html_path.write_text("tampered")
    assert "error" in _read_prepared_source_impl(deps, 1, 2)


def test_graphical_signature_retains_source_region_without_invented_prose(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def signature(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["non_text_regions"] = [{"reason": "handwritten_signature", "bbox": [.2, .4, .4, .5]}]
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=signature))
    region = prepared.pages[0]["non_text_regions"][0]
    assert region["reason"] == "handwritten_signature"
    assert region["evidence"]["page"] == 1
    assert region["evidence"]["source_file"] == "uploaded.pdf"
    assert len(region["evidence"]["source_sha256"]) == 64
    assert "[Signature]" not in prepared.source_html_path.read_text()


def test_stamp_text_is_not_a_permitted_non_text_exclusion(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def signature(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["non_text_regions"] = [{"reason": "stamp", "bbox": [.2, .4, .4, .5]}]
        return result
    with pytest.raises(PreparationError, match="preparation did not finish"):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=signature))


def test_localized_repair_receives_previous_html_and_exact_verifier_issues(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    initial = '<h2>Directors</h2><p>Unchanged wording.</p><p>Name typo</p>'
    repaired = '<h2>Directors</h2><p>Unchanged wording.</p><p>Name correct</p>'
    captures = verifies = 0
    async def localized(stage, images, context):
        nonlocal captures, verifies
        result = await base(stage, images, context)
        if stage == "capturing":
            captures += 1
            if captures == 1:
                assert context["previous_html"] == ""
                result["html"] = initial
            else:
                assert context["previous_html"] == initial
                assert context["issues"] == ["Replace Name typo with Name correct as printed."]
                result["html"] = repaired
        if stage == "verifying":
            verifies += 1
            if verifies == 1:
                result.update(verified=False, issues=["Replace Name typo with Name correct as printed."])
            else:
                assert context["html"] == repaired
                assert len(images) == 5
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=localized))
    assert captures == verifies == 2
    assert repaired in prepared.source_html_path.read_text()


def test_verifier_presentation_allowances_keep_text_and_table_structure_mandatory():
    from ingest.document_preparation import _PROMPTS
    prompt = _PROMPTS["verifying"]
    assert "not pixel-identical" in prompt
    assert "line wrapping" in prompt
    assert "every label retains its exact" in prompt
    assert "rowspan/colspan" in prompt
    assert "punctuation still must match the source exactly" in prompt
    assert "Never certify from candidate alone" in prompt


def test_administrative_stamp_only_page_is_accounted_without_invented_text(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def stamp(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["html"] = ""
            result["non_text_regions"] = [{"reason": "administrative_stamp", "bbox": [.1, .1, .4, .4]}]
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=stamp))
    assert prepared.pages[0]["html"] == ""
    assert prepared.blocks == []
    assert prepared.pages[0]["non_text_regions"][0]["evidence"]["source_file"] == "uploaded.pdf"


def test_pdf_page_furniture_is_excluded_but_substantive_headings_and_footnotes_survive(tmp_path):
    from ingest.document_preparation import PreparationReceipt
    path = pdf(tmp_path, 1)
    original = path.read_bytes()
    content = '<h2>2. Inventories</h2><table><tr><td>Raw materials</td><td>115</td></tr></table><p>* Includes goods in transit.</p>'
    base, calls = caller()
    async def capture_without_furniture(stage, images, context):
        result = await base(stage, images, context)
        if stage in {"capturing", "verifying"}:
            assert context["native_word_source"] is False
        if stage == 'verifying':
            assert len(images) == 1  # Ordinary furniture needs no extra magnified pass.
            assert 'page furniture' in context['instruction']
        if stage == "capturing":
            result.update(html=content, non_text_regions=[
                {"reason": "page_header", "bbox": [.1, .01, .8, .08]},
                {"reason": "page_number", "bbox": [.8, .92, .9, .95]},
                {"reason": "page_footer", "bbox": [.1, .96, .8, .99]},
            ])
            return PreparationReceipt.model_validate(result).model_dump()
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=capture_without_furniture))
    assert len(prepared.blocks) == 3
    assert prepared.pages[0]["verified"] is True
    assert '<h2>2. Inventories</h2>' in prepared.source_html_path.read_text()
    assert 'Includes goods in transit' in prepared.source_html_path.read_text()
    assert {r['reason'] for r in prepared.pages[0]['non_text_regions']} == {'page_header', 'page_footer', 'page_number'}
    assert all(r['evidence']['page'] == 1 for r in prepared.pages[0]['non_text_regions'])
    assert len([stage for stage, _ in calls if stage == 'capturing']) == 1
    assert path.read_bytes() == original


def test_capture_and_verification_agree_on_page_furniture_and_pdf_index():
    from ingest.document_preparation import _COMMON, _PROMPTS
    assert 'Exclude printed page numbers' in _COMMON
    assert 'page is the 1-based PDF position' in _COMMON
    assert 'statement titles' in _COMMON and 'footnotes' in _COMMON
    assert 'omit running furniture' not in _PROMPTS['capturing']


def test_guessed_text_is_not_certified_even_when_verifier_agrees(tmp_path):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def guess(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result.update(readable=False, complete=True, html="<p>Receivables</p>",
                          uncertainties=[{"reason": "Faint word reconstructed", "observed_text": "Recei...",
                                          "reconstructed_text": "Receivables", "bbox": [.1, .1, .5, .2]}])
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=guess))
    assert prepared.pages[0]["verified"] is False
    assert prepared.blocks[0]["locator"]["capture_method"] == "reconstructed"
    assert "Receivables" in prepared.source_html_path.read_text()


def test_boundary_uncertainty_and_invalid_ids_preserve_draft(tmp_path):
    path = pdf(tmp_path)
    base, _ = caller()
    async def uncertain(stage, images, context):
        result = await base(stage, images, context)
        if stage == "joining":
            result.update(verified=False, readable=False, issues=["Faint boundary; likely separate paragraphs"])
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=uncertain))
    assert all(page["capture_status"] == "best_effort" for page in prepared.pages)
    assert all(not page["verified"] for page in prepared.pages)
    (tmp_path / "preparation.json").unlink()
    checkpoint_path = next(tmp_path.glob("preparation-checkpoint-*.json"))
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["boundary_receipts"] = {}
    checkpoint_path.write_text(json.dumps(checkpoint))
    async def invalid(stage, images, context):
        result = await uncertain(stage, images, context)
        if stage == "joining":
            result["links"] = [{"from_block_id": "missing", "to_block_id": "invalid", "separator": " "}]
        return result
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=invalid))
    assert all(b["continues_block_id"] is None for b in result.blocks)
    assert all(p["capture_status"] == "best_effort" for p in result.pages)


def test_boundary_explicit_uncertainty_wins_over_positive_booleans(tmp_path):
    path = pdf(tmp_path)
    base, _ = caller()
    async def uncertain(stage, images, context):
        result = await base(stage, images, context)
        if stage == "joining":
            result["uncertainties"] = [{"reason": "Boundary relation guessed"}]
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=uncertain))
    assert all(p["verified"] is False for p in prepared.pages)
    assert all(b["locator"]["capture_uncertain"] for b in prepared.blocks)


def test_ownership_explicit_uncertainty_updates_blocks_and_sidecar(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path, 1)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assignments = [{"block_id": b["block_id"], "owner_kind": "note",
        "source_note_id": "n2", "source_note_num": "2", "source_note_title": "Policies",
        "uncertainties": [{"reason": "Faint note number inferred"}]} for b in prepared.blocks]
    prepared = asyncio.run(reconcile_prepared_inventory(prepared, assignments=assignments))
    assert prepared.pages[0]["verified"] is False
    assert prepared.blocks[0]["locator"]["capture_uncertain"]
    assert json.loads((tmp_path / "source_meta.json").read_text())["verified"] is False


@pytest.mark.parametrize("readable", [True, False])
def test_unfinished_assessment_is_an_operational_failure_not_readability_uncertainty(tmp_path, readable):
    path = pdf(tmp_path, 1)
    base, _ = caller()
    async def unfinished(stage, images, context):
        result = await base(stage, images, context)
        if stage == "verifying":
            result.update(complete=False, verified=False, readable=readable,
                          uncertainties=[{"reason": "The remaining region was not assessed"}])
        return result
    with pytest.raises(PreparationError, match="assessment did not complete"):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=unfinished))
    assert not (tmp_path / "preparation.json").exists()


def test_short_meaningful_page_is_captured_not_declared_blank(tmp_path):
    path = tmp_path / "uploaded.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Nil.", fontsize=8)
        doc.save(path)
    base, calls = caller()
    async def short_text(stage, images, context):
        receipt = await base(stage, images, context)
        if stage == "capturing":
            receipt["html"] = "<p>Nil.</p>"
        return receipt
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=short_text))
    assert "Nil." in result.source_html_path.read_text()
    assert [stage for stage, _ in calls] == ["capturing", "verifying"]


def test_capture_queue_wait_does_not_consume_request_timeout(tmp_path):
    from contextlib import asynccontextmanager
    path = pdf(tmp_path, 1)
    base, calls = caller()
    class QueuedBudget:
        @asynccontextmanager
        async def slot(self):
            await asyncio.sleep(0.04)
            yield
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base,
        page_timeout_s=0.01, overall_timeout_s=5, _budget=QueuedBudget()))
    assert result.pages[0]["verified"] is True
    assert [stage for stage, _ in calls] == ["capturing", "verifying"]


def test_relationship_verification_does_not_recertify_accepted_uncertain_words():
    from ingest.document_preparation import _COMMON, _PROMPTS
    assert "joining, verified assesses ONLY continuation" in _COMMON
    assert "must not make that relationship assessment fail or retry" in _COMMON
    assert "this stage does not recertify individual words or glyphs" in _PROMPTS["joining"]


def test_model_linked_list_and_next_item_paragraph_preserve_structure_as_related_blocks(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path)
    base, _ = caller(link=True)
    first = '<ol type="a"><li>First item<ol type="i"><li>Nested item</li></ol></li></ol>'
    second = '<p>(b) Next item wording.</p>'
    async def source(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["html"] = first if context["page"] == 1 else second
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=source))
    left, right = prepared.blocks
    assert left["canonical_html"] == first
    assert right["canonical_html"] == second
    assert right["continues_block_id"] is None
    assert left["locator"]["required_related_block_ids"] == [right["block_id"]]
    assert right["locator"]["required_related_block_ids"] == [left["block_id"]]
    assignments = [{"block_id": b["block_id"], "owner_kind": "note",
        "source_note_id": "n2", "source_note_num": "2", "source_note_title": "Policies"}
        for b in prepared.blocks]
    reconciled = asyncio.run(reconcile_prepared_inventory(prepared, assignments=assignments))
    assert reconciled.blocks[1]["locator"]["required_related_block_ids"] == [left["block_id"]]


def test_table_paragraph_cross_kind_link_remains_invalid(tmp_path):
    path = pdf(tmp_path)
    base, _ = caller(link=True)
    async def invalid(stage, images, context):
        result = await base(stage, images, context)
        if stage == "capturing":
            result["html"] = '<table><tr><td>Value</td></tr></table>' if context["page"] == 1 else '<p>Separate prose.</p>'
        return result
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=invalid))
    assert len(result.blocks) == 2
    assert all(b["continues_block_id"] is None for b in result.blocks)


def test_same_page_related_blocks_keep_valid_metadata_ownership(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path, 1)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    left, right = prepared.blocks
    assignments = [{"block_id": b["block_id"], "owner_kind": "metadata", "reason_code": "DOCUMENT_METADATA"}
                   for b in prepared.blocks]
    assignments[0]["required_related_block_ids"] = [right["block_id"]]
    result = asyncio.run(reconcile_prepared_inventory(prepared, assignments=assignments))
    assert all(b["owner_kind"] == "metadata" for b in result.blocks)
    assert result.blocks[0]["locator"]["required_related_block_ids"] == [right["block_id"]]
    assert result.blocks[1]["locator"]["required_related_block_ids"] == [left["block_id"]]


def test_same_page_owner_conflict_is_rejected_without_publishing(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path, 1)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    before = prepared.metadata_path.read_bytes()
    left, right = prepared.blocks
    assignments = [
        {"block_id": left["block_id"], "owner_kind": "metadata", "reason_code": "DOCUMENT_METADATA",
         "required_related_block_ids": [right["block_id"]]},
        {"block_id": right["block_id"], "owner_kind": "note", "source_note_id": "n1", "source_note_num": "1"},
    ]
    with pytest.raises(PreparationError, match="crosses source note ownership"):
        asyncio.run(reconcile_prepared_inventory(prepared, assignments=assignments))
    assert prepared.metadata_path.read_bytes() == before


def test_capture_combines_rotation_and_reuses_each_render(tmp_path, monkeypatch):
    import ingest.document_preparation as module
    path = pdf(tmp_path, 2)
    original_render = module._render_page
    renders = []
    def render(path, page, rotation=0):
        renders.append((page, rotation))
        return original_render(path, page, rotation)
    monkeypatch.setattr(module, "_render_page", render)
    base, calls = caller(rotation=90)
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assert not any(stage == "orienting" for stage, _ in calls)
    assert sorted(renders) == [(1, 0), (1, 90), (2, 0), (2, 90)]
    assert result.rotation_corrections == {1: 90, 2: 90}
    assert sum(stage == "capturing" for stage, _ in calls) == 2
    assert sum(stage == "verifying" for stage, _ in calls) == 2


def test_join_starts_while_later_page_is_still_capturing(tmp_path):
    path = pdf(tmp_path, 3)
    async def run():
        joined = asyncio.Event()
        base, _ = caller()
        async def overlapping(stage, images, context):
            if stage == "capturing" and context["page"] == 3:
                await asyncio.wait_for(joined.wait(), 3)
            if stage == "joining" and context["page"] == 2:
                joined.set()
            return await base(stage, images, context)
        await prepare_document(path, None, model_name="fake", concurrency=3, _caller=overlapping)
        assert joined.is_set()
    asyncio.run(run())


def test_explicit_independent_edge_absence_skips_join_but_unknown_does_not(tmp_path):
    path = pdf(tmp_path, 2)
    base, calls = caller()
    async def edges(stage, images, context):
        result = await base(stage, images, context)
        if stage in {"capturing", "verifying"}:
            result.update(continues_from_previous=False, continues_to_next=False)
        return result
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=edges))
    assert not any(stage == "joining" for stage, _ in calls)
    other = tmp_path / "unknown"
    other.mkdir()
    unknown_path = pdf(other, 2)
    base, calls = caller()
    asyncio.run(prepare_document(unknown_path, None, model_name="fake", _caller=base))
    assert any(stage == "joining" for stage, _ in calls)


def test_completed_boundary_receipt_reused_after_interruption(tmp_path):
    path = pdf(tmp_path, 2)
    base, calls = caller()
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    (tmp_path / "preparation.json").unlink()
    async def no_calls(*args):
        raise AssertionError("A completed page or boundary was unnecessarily repeated")
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=no_calls))
    assert result.page_count == 2
    assert sum(stage == "joining" for stage, _ in calls) == 1


def test_supplied_document_map_assignments_make_no_render_or_model_calls(tmp_path, monkeypatch):
    import ingest.document_preparation as module
    path = pdf(tmp_path, 1)
    base, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    before = len(json.loads(prepared.metadata_path.read_text())["calls"])
    assignments = [{"block_id": block["block_id"], "owner_kind": "note", "source_note_id": "n1",
                    "source_note_num": "1", "source_note_title": "Disclosure",
                    "uncertainties": [{"reason": "Faint title reconstructed"}]} for block in prepared.blocks]
    def no_render(*args):
        raise AssertionError("A supplied complete map must not render pages")
    async def no_call(*args):
        raise AssertionError("A supplied complete map must not call a model")
    monkeypatch.setattr(module, "_render_page", no_render)
    monkeypatch.setattr(module, "_request_model", no_call)
    result = asyncio.run(module.reconcile_prepared_inventory(prepared, assignments=assignments))
    assert all(block["source_note_id"] == "n1" for block in result.blocks)
    assert all(block["locator"]["capture_uncertain"] for block in result.blocks)
    assert len(json.loads(result.metadata_path.read_text())["calls"]) == before
    with pytest.raises(PreparationError, match="exactly once"):
        asyncio.run(module.reconcile_prepared_inventory(result, assignments=assignments[:-1]))


@pytest.mark.asyncio
async def test_cancel_during_map_application_keeps_original_ledger(tmp_path):
    from ingest.document_preparation import reconcile_prepared_inventory
    path = pdf(tmp_path, 2)
    call, _ = caller()
    prepared = await prepare_document(path, None, model_name="fake", _caller=call)
    before = prepared.metadata_path.read_bytes()
    assignments = [{"block_id": b["block_id"], "owner_kind": "note", "source_note_id": "n1"}
                   for b in prepared.blocks]
    def stop(progress):
        if progress["completed"] == 1:
            asyncio.current_task().cancel("stop")
    task = asyncio.create_task(reconcile_prepared_inventory(prepared, assignments=assignments, on_progress=stop))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert prepared.metadata_path.read_bytes() == before


def test_invalid_boundary_links_are_replaced_by_cached_uncertainty(tmp_path):
    path = pdf(tmp_path, 2)
    base, _ = caller()
    async def invalid(stage, images, context):
        result = await base(stage, images, context)
        if stage == "joining":
            result["links"] = [{"from_block_id": "bad", "to_block_id": "unknown", "separator": " "}]
        return result
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=invalid))
    assert all(b["continues_block_id"] is None for b in result.blocks)
    assert all(p["capture_status"] == "best_effort" for p in result.pages)
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert all(r["links"] == [] and r["uncertainties"] for r in checkpoint["boundary_receipts"].values())
    resumed, calls = caller()
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=resumed))
    assert calls == []


def test_invalid_cached_boundary_is_discarded_before_reuse(tmp_path):
    path = pdf(tmp_path, 2)
    base, _ = caller()
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    (tmp_path / "preparation.json").unlink()
    checkpoint_path = next(tmp_path.glob("preparation-checkpoint-*.json"))
    checkpoint = json.loads(checkpoint_path.read_text())
    receipt = next(iter(checkpoint["boundary_receipts"].values()))
    receipt["links"] = [{"from_block_id": "bad", "to_block_id": "bad", "separator": " "}]
    checkpoint_path.write_text(json.dumps(checkpoint))
    resumed, calls = caller()
    asyncio.run(prepare_document(path, None, model_name="fake", _caller=resumed))
    assert [stage for stage, _ in calls] == ["joining"]


def test_preparation_defaults_to_fifteen_shared_requests(tmp_path):
    path = pdf(tmp_path, 18)
    async def run():
        ready = asyncio.Event()
        active = peak = initial_captures = 0
        base, _ = caller()
        async def parallel(stage, images, context):
            nonlocal active, peak, initial_captures
            active += 1
            peak = max(peak, active)
            try:
                if stage == "capturing":
                    initial_captures += 1
                    if initial_captures == 15:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                return await base(stage, images, context)
            finally:
                active -= 1
        await prepare_document(path, None, model_name="fake", _caller=parallel)
        assert peak == 15
    asyncio.run(run())


def test_non_text_bbox_schema_rejects_pixel_coordinates_without_guessing_units():
    from pydantic import ValidationError
    from ingest.document_preparation import NonTextRegion
    with pytest.raises(ValidationError, match="normalized fractions"):
        NonTextRegion(reason="scanner_noise", bbox=[77, 19, 84, 25])
    with pytest.raises(ValidationError, match="normalized fractions"):
        NonTextRegion(reason="scanner_noise", bbox=[1598, 86, 1604, 91])
    with pytest.raises(ValidationError, match="nonempty normalized rectangle"):
        NonTextRegion(reason="scanner_noise", bbox=[.3, .3, .2, .5])
    assert NonTextRegion(reason="scanner_noise", bbox=[.1, .2, .3, .4]).bbox == [.1, .2, .3, .4]


def test_structured_model_retries_invalid_bbox_with_feedback_preserving_html():
    from pydantic_ai.messages import ModelResponse, ToolCallPart, RetryPromptPart
    from pydantic_ai.models.function import FunctionModel
    from ingest.document_preparation import _request_model
    calls = 0
    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            feedback = [part for message in messages for part in message.parts if isinstance(part, RetryPromptPart)]
            assert feedback
            assert "normalized fractions" in str(feedback[-1].content)
        output = {"complete": True, "readable": True, "html": "<p>Unchanged disclosure.</p>",
                  "non_text_regions": [{"reason": "scanner_noise", "bbox": [77, 19, 84, 25] if calls == 1 else [.1, .2, .3, .4]}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])
    receipt = asyncio.run(_request_model(FunctionModel(respond), "capturing", [], {"page": 1}))
    assert calls == 2
    assert receipt["html"] == "<p>Unchanged disclosure.</p>"
    assert receipt["non_text_regions"][0]["bbox"] == [.1, .2, .3, .4]


def test_native_failure_after_join_keeps_capture_checkpoint_reusable(tmp_path, monkeypatch):
    path = pdf(tmp_path, 2)
    (tmp_path / "uploaded.docx").write_bytes(b"native-fixture")
    monkeypatch.setattr("ingest.docx_html.extract_docx_html", lambda _: "<h2>Note</h2><p>Text 1</p><h2>Note</h2><p>Text 2</p>")
    base, calls = caller(link=True)
    async def fail_native(stage, images, context):
        if stage == "native_verifying":
            raise RuntimeError("temporary native comparison transport failure")
        return await base(stage, images, context)
    with pytest.raises(RuntimeError):
        asyncio.run(prepare_document(path, None, model_name="fake", _caller=fail_native))
    checkpoint_path = next(tmp_path.glob("preparation-checkpoint-*.json"))
    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["pages"]["2"]["blocks"][-1]["continues_block_id"] is None
    # Older checkpoints may already contain the same relation; reuse remains idempotent.
    checkpoint["pages"]["2"]["blocks"][-1]["continues_block_id"] = checkpoint["pages"]["1"]["blocks"][-1]["block_id"]
    checkpoint_path.write_text(json.dumps(checkpoint))
    before = len(calls)
    result = asyncio.run(prepare_document(path, None, model_name="fake", _caller=base))
    assert [stage for stage, _ in calls[before:]] == ["native_verifying"]
    assert result.blocks[-1]["continues_block_id"] == result.blocks[1]["block_id"]


def test_boundary_index_addresses_body_after_four_header_blocks(tmp_path):
    path = pdf(tmp_path)
    seen = []
    async def call(stage, images, context):
        result = {"complete": True, "readable": True, "verified": True, "rotation": 0, "links": []}
        if stage == "capturing":
            result["html"] = ("<p>Disclosure fragment</p>" if context["page"] == 1 else
                "<p>Registration</p><h1>Company</h1><h2>Note continued</h2>"
                "<h3>Policy continued</h3><h4>Section continued</h4><p>continued body.</p>")
        if stage == "joining":
            seen.append(context)
            assert len(context["next_blocks"]) == 4
            index = context["next_block_index"]
            assert len(index) == 6
            target = next(block for block in index if block["text"] == "continued body.")
            assert target["block_id"] not in {block["block_id"] for block in context["next_blocks"]}
            result["links"] = [{"from_block_id": context["previous_block_index"][0]["block_id"],
                                "to_block_id": target["block_id"], "separator": " "}]
        return result
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    assert len(seen) == 1
    assert prepared.blocks[-1]["continues_block_id"] == prepared.blocks[0]["block_id"]
    assert prepared.blocks[1]["continues_block_id"] is None


def test_previous_contract_completed_preparation_is_not_reused(tmp_path):
    from ingest.document_preparation import CONTRACT_VERSION
    path = pdf(tmp_path, pages=1)
    call, _ = caller()
    prepared = asyncio.run(prepare_document(path, None, model_name="fake", _caller=call))
    data = json.loads(prepared.metadata_path.read_text())
    data["contract_version"] = CONTRACT_VERSION - 1
    prepared.metadata_path.write_text(json.dumps(data))
    assert read_prepared_document(path, model_name="fake") is None


def test_failed_output_retries_persist_all_completed_response_usage(tmp_path):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    responses = []
    def invalid_receipt(messages, info):
        responses.append(True)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "non_text_regions": [{"reason": "scanner_noise", "bbox": [20, 20, 40, 40]}],
        })], usage=RequestUsage(input_tokens=10, output_tokens=5))

    with pytest.raises(PreparationError, match="preparation did not finish"):
        asyncio.run(prepare_document(pdf(tmp_path, 1), FunctionModel(invalid_receipt), model_name="fake"))
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    calls = checkpoint["calls"]
    assert len(responses) == 6  # Two capture attempts, each exhausting three responses.
    assert all(call["status"] == "failed" for call in calls)
    assert sum(call["usage"]["prompt_tokens"] for call in calls) == 60
    assert sum(call["usage"]["completion_tokens"] for call in calls) == 30
    assert sum(call["usage"]["total_tokens"] for call in calls) == 90
    assert not (tmp_path / "preparation.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_interrupted_retry_persists_completed_response_usage(tmp_path, stop):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    retry_started = asyncio.Event()
    responses = 0
    async def interrupted_receipt(messages, info):
        nonlocal responses
        responses += 1
        if responses % 2 == 0:
            retry_started.set()
            await asyncio.Future()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "non_text_regions": [{"reason": "scanner_noise", "bbox": [20, 20, 40, 40]}],
        })], usage=RequestUsage(input_tokens=10, output_tokens=5))

    worker = asyncio.create_task(prepare_document(
        pdf(tmp_path, 1), FunctionModel(interrupted_receipt), model_name="fake",
        page_timeout_s=0.5 if stop == "timeout" else 5,
    ))
    await asyncio.wait_for(retry_started.wait(), 3)
    if stop == "cancel":
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    else:
        with pytest.raises(PreparationError, match="preparation did not finish"):
            await asyncio.wait_for(worker, 3)
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    calls = checkpoint["calls"]
    assert len(calls) == (1 if stop == "cancel" else 2)
    assert responses == (2 if stop == "cancel" else 4)
    assert all(call["status"] == "failed" for call in calls)
    assert all(call["usage"]["prompt_tokens"] == 10 for call in calls)
    assert all(call["usage"]["completion_tokens"] == 5 for call in calls)
    assert all(call["usage"]["total_tokens"] == 15 for call in calls)
    assert not (tmp_path / "preparation.json").exists()
