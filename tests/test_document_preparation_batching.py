"""Offline coverage of batched capture and independent page verification."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import fitz
import pytest

from ingest import document_preparation as preparation


@pytest.fixture(autouse=True)
def small_renders(monkeypatch):
    # The real page scheduler, schemas, checkpoints and PDF publication still run.
    monkeypatch.setattr(preparation, "_render_page",
                        lambda path, page, rotation=0: f"{page}:{rotation}".encode())
    monkeypatch.setattr(preparation, "_render_is_blank", lambda image: False)
    monkeypatch.setattr(preparation, "_focused_views",
                        lambda path, page, rotation: [f"focus:{page}:{rotation}".encode()])


def source(directory, count):
    directory.mkdir(exist_ok=True)
    path = directory / "uploaded.pdf"
    with fitz.open() as document:
        for page in range(1, count + 1):
            document.new_page().insert_text((72, 72), f"Page {page}")
        document.save(path)
    return path


def receipt(page):
    return {"page": page, "complete": True, "readable": True, "verified": True,
            "rotation": 0, "html": f"<p>Content {page}</p>",
            "continues_from_previous": False, "continues_to_next": False}


def response(context):
    if "pages" in context:
        # Deliberately reverse model output: source ownership cannot depend on order.
        return {"pages": [receipt(p["page"]) for p in reversed(context["pages"])],
                "usage": {"total_tokens": 20}}
    return {**receipt(context["page"]), "usage": {"total_tokens": 10}}


def run(path, call, **kwargs):
    return asyncio.run(preparation.prepare_document(
        path, None, model_name="fake", _caller=call, **kwargs))


@pytest.mark.parametrize("count", [1, 2, 3, 6])
def test_default_pairs_pages_and_flushes_odd_page(tmp_path, count):
    calls = []
    async def call(stage, images, context):
        calls.append((stage, context))
        for page in context.get("pages", []):
            assert images[page["image_indexes"][0]] == f"{page['page']}:0".encode()
        return response(context)

    prepared = run(source(tmp_path, count), call)
    assert [p["html"] for p in prepared.pages] == [f"<p>Content {i}</p>" for i in range(1, count + 1)]
    captures = [context for name, context in calls if name == "capturing"]
    verifications = [context for name, context in calls if name == "verifying"]
    assert len(captures) == (count + 1) // 2
    assert sum(len(c.get("pages", [c])) for c in captures) == count
    assert len(verifications) == count
    assert all("pages" not in context for context in verifications)
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert len(checkpoint["calls"]) == len(calls)
    assert sum(c["usage"]["total_tokens"] for c in checkpoint["calls"]) == count * 20


def test_wrong_page_capture_is_repaired_by_independent_verification(tmp_path):
    """A page-labelled batch receipt can still contain its neighbour's text."""
    calls = []

    async def call(stage, images, context):
        calls.append((stage, context))
        if stage == "capturing" and "pages" in context:
            pages = context["pages"]
            return {"pages": [
                {**receipt(page["page"]), "html": f"<p>Content {other['page']}</p>"}
                for page, other in zip(pages, reversed(pages))
            ]}
        if stage == "verifying" and "pages" in context:
            return response(context)
        result = response(context)
        if stage == "verifying":
            page = context["page"]
            result["verified"] = result["complete"] = (
                context["html"] == f"<p>Content {page}</p>"
            )
            if not result["verified"]:
                result["issues"] = ["The candidate belongs to another page."]
        return result

    prepared = run(source(tmp_path, 2), call)
    assert [page["html"] for page in prepared.pages] == [
        "<p>Content 1</p>", "<p>Content 2</p>",
    ]
    assert any(stage == "verifying" and context.get("page") == 1
               for stage, context in calls)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "invalid", "foreign"])
def test_bad_capture_receipt_retries_only_affected_page(tmp_path, defect):
    singles, batch_responses = [], []
    async def call(name, images, context):
        result = response(context)
        if name == "capturing" and "pages" in context:
            good, bad = receipt(1), receipt(2)
            if defect == "missing":
                result["pages"] = [good]
            elif defect == "duplicate":
                result["pages"] = [good, bad, {**bad, "html": "<p>Rejected duplicate content</p>"}]
            elif defect == "foreign":
                result["pages"] = [good, receipt(99)]
            else:
                bad["non_text_regions"] = [{"reason": "scanner_noise", "bbox": [0, 0, 900, 900]}]
                result["pages"] = [good, bad]
            batch_responses.append(result)
        elif name == "capturing":
            singles.append(context["page"])
        return result

    prepared = run(source(tmp_path, 2), call)
    assert singles == [2]
    assert all(page["verified"] for page in prepared.pages)
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    batch_attempts = [attempts[0] for attempts in checkpoint["page_attempts"].values()]
    assert all(attempt["batch"] for attempt in batch_attempts)
    assert len({attempt["evidence_file"] for attempt in batch_attempts}) == 1
    evidence = json.loads((tmp_path / batch_attempts[0]["evidence_file"]).read_text())
    assert evidence["batch_response"] == batch_responses[0]
    assert sorted(page["page"] for page in evidence["page_contexts"]) == [1, 2]
    assert "capture" not in evidence
    # Rejected payloads remain diagnostic; single-page fallback owns the output.
    assert [page["html"] for page in prepared.pages] == ["<p>Content 1</p>", "<p>Content 2</p>"]


@pytest.mark.parametrize("failure", ["timeout", "malformed", "null", "provider"])
def test_failed_batch_falls_back_to_single_pages(tmp_path, failure):
    batches, singles = [], []
    async def call(stage, images, context):
        if stage == "capturing":
            if "pages" in context:
                batches.append(context)
                if failure == "timeout":
                    raise TimeoutError("offline timeout")
                if failure == "provider":
                    raise RuntimeError("offline provider error")
                return {"pages": None if failure == "null" else "truncated"}
            singles.append(context["page"])
        return response(context)

    prepared = run(source(tmp_path, 2), call)
    assert sorted(singles) == [1, 2]
    assert len(batches) == (2 if failure == "timeout" else 1)
    assert len(prepared.pages) == 2
    if failure in {"malformed", "null"}:
        # The malformed batch is diagnosed as delivered, not as a failed call,
        # and its complete evidence is distinct from each accepted capture.
        checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
        batch_record = next(r for r in checkpoint["calls"] if r.get("pages"))
        assert batch_record["status"] == "succeeded" and "error_type" not in batch_record
        for attempts in checkpoint["page_attempts"].values():
            assert [a["stage"] for a in attempts] == ["capturing", "capturing", "verifying"]
            assert attempts[0]["batch"] is True
            assert not attempts[1].get("batch")
        references = {attempts[0]["evidence_file"] for attempts in checkpoint["page_attempts"].values()}
        assert len(references) == 1
        evidence = json.loads((tmp_path / references.pop()).read_text())
        assert evidence["batch_response"] == {"pages": None if failure == "null" else "truncated"}
        assert "capture" not in evidence


def test_blank_and_resumed_pages_do_not_wait_for_partners(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "_render_is_blank", lambda image: image == b"2:0")
    calls = []
    async def call(stage, images, context):
        calls.append((stage, context))
        return response(context)

    path = source(tmp_path, 3)
    prepared = run(path, call)
    assert prepared.pages[1]["kind"] == "blank"
    assert all(p["page"] != 2 for _, c in calls for p in c.get("pages", [c]))
    prepared.metadata_path.unlink()
    checkpoint_path = next(tmp_path.glob("preparation-checkpoint-*.json"))
    checkpoint = json.loads(checkpoint_path.read_text())
    del checkpoint["pages"]["3"]
    checkpoint_path.write_text(json.dumps(checkpoint))
    calls.clear()
    resumed = run(path, call)
    assert [(stage, c["page"]) for stage, c in calls] == [("capturing", 3), ("verifying", 3)]
    assert len(resumed.pages) == 3


def test_continuations_across_batch_boundaries_keep_source_page_ids(tmp_path):
    joined = []
    async def call(stage, images, context):
        if stage == "joining":
            previous = context["previous_blocks"][-1]["block_id"]
            current = context["next_blocks"][0]["block_id"]
            joined.append(context["page"])
            return {"complete": True, "readable": True, "verified": True,
                    "links": [{"from_block_id": previous, "to_block_id": current, "separator": " "}]}
        result = response(context)
        for entry in result.get("pages", [result]):
            entry.update(continues_from_previous=entry["page"] > 1,
                         continues_to_next=entry["page"] < 3)
        return result

    prepared = run(source(tmp_path, 3), call)
    assert sorted(joined) == [2, 3]
    assert prepared.blocks[1]["continues_block_id"] == prepared.blocks[0]["block_id"]
    assert prepared.blocks[2]["continues_block_id"] == prepared.blocks[1]["block_id"]
    assert [block["page"] for block in prepared.blocks] == [1, 2, 3]


def test_rotation_and_uncertainty_stay_with_their_pages(tmp_path):
    async def call(stage, images, context):
        result = response(context)
        entries = result.get("pages", [result])
        for entry in entries:
            if entry["page"] == 1:
                entry["rotation"] = 90
            if entry["page"] == 2 and stage == "capturing":
                entry["uncertainties"] = [{"reason": "Faint wording", "observed_text": "?"}]
        if stage == "verifying":
            for page in context.get("pages", [context]):
                index = page.get("image_indexes", [0])[0]
                assert images[index] == f"{page['page']}:{90 if page['page'] == 1 else 0}".encode()
        return result

    prepared = run(source(tmp_path, 2), call)
    assert prepared.rotation_corrections == {1: 90}
    assert prepared.pages[0]["verified"] is True
    assert prepared.pages[1]["verified"] is False
    assert prepared.pages[1]["uncertainties"][0]["reason"] == "Faint wording"


def test_incomplete_capture_repairs_only_that_page_with_focused_views(tmp_path):
    singles = []
    async def call(stage, images, context):
        result = response(context)
        if stage == "capturing" and "pages" in context:
            for entry in result["pages"]:
                if entry["page"] == 2:
                    entry["complete"] = False
        if stage == "capturing" and "pages" not in context:
            singles.append(context["page"])
            assert context["best_effort"] is True
            assert images == [b"2:0", b"focus:2:0"]
        return result

    prepared = run(source(tmp_path, 2), call)
    assert singles == [2]
    assert all(page["verified"] for page in prepared.pages)


def test_focused_images_in_verification_have_explicit_page_ownership(tmp_path):
    checked = []
    async def call(stage, images, context):
        result = response(context)
        if stage == "capturing":
            for entry in result.get("pages", [result]):
                entry["non_text_regions"] = [{"reason": "scanner_noise", "bbox": [0.0, 0.0, 0.1, 0.1]}]
        if stage == "verifying":
            number = context["page"]
            assert images == [f"{number}:0".encode(), f"focus:{number}:0".encode()]
            checked.append(number)
        return result

    run(source(tmp_path, 2), call)
    assert sorted(checked) == [1, 2]


@pytest.mark.asyncio
async def test_partial_verification_saves_companion_before_cancellation_and_resumes(tmp_path):
    budget = preparation.RequestBudget(15)
    path = source(tmp_path, 2)
    waiting = asyncio.Event()
    async def call(stage, images, context):
        result = response(context)
        if stage == "verifying" and "pages" in context:
            return {"pages": [receipt(1)]}
        if stage == "verifying" and context["page"] == 2:
            waiting.set()
            await asyncio.Future()
        return result

    worker = asyncio.create_task(preparation.prepare_document(
        path, None, model_name="fake", _caller=call, _budget=budget))
    await asyncio.wait_for(waiting.wait(), 3)
    async def saved_companion():
        while True:
            files = list(tmp_path.glob("preparation-checkpoint-*.json"))
            if files and json.loads(files[0].read_text()).get("pages", {}).get("1"):
                return
            await asyncio.sleep(0.01)
    await asyncio.wait_for(saved_companion(), 3)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert budget._active == 0
    resumed_calls = []
    async def resume(stage, images, context):
        resumed_calls.append((stage, context["page"]))
        return response(context)
    result = await preparation.prepare_document(path, None, model_name="fake", _caller=resume)
    assert resumed_calls == [("capturing", 2), ("verifying", 2)]
    assert len(result.pages) == 2


@pytest.mark.parametrize("concurrency", [0, 16, True, 1.5])
def test_invalid_request_limits_are_rejected(tmp_path, concurrency):
    with pytest.raises(ValueError, match="between 1 and 15"):
        run(tmp_path / "not-needed.pdf", None, concurrency=concurrency)


@pytest.mark.parametrize("failed_stage", ["capturing", "verifying"])
def test_persistent_incomplete_page_never_activates_document(tmp_path, failed_stage):
    calls = []
    budget = preparation.RequestBudget(1)
    async def call(stage, images, context):
        calls.append((stage, context))
        result = response(context)
        if stage == failed_stage:
            for entry in result.get("pages", [result]):
                if entry["page"] == 2:
                    entry.update(complete=False, verified=False)
        return result

    with pytest.raises(preparation.PreparationError):
        run(source(tmp_path, 2), call, concurrency=1, _budget=budget)
    assert len(calls) <= 6
    assert budget._active == 0 and not budget._waiting
    assert not (tmp_path / "preparation.json").exists()


@pytest.mark.parametrize("failure", ["timeout", "provider"])
@pytest.mark.parametrize("count", [2, 6])
def test_exhausted_batch_and_single_timeouts_are_bounded(tmp_path, count, failure):
    # Exhausted retries and unexpected provider errors are systemic: remaining
    # pages stop instead of each exhausting its own retries.
    budget = preparation.RequestBudget(1)
    calls = []
    async def call(stage, images, context):
        calls.append(context)
        if failure == "provider":
            raise RuntimeError("offline provider error")
        raise TimeoutError("offline persistent timeout")

    with pytest.raises(preparation.PreparationError if failure == "timeout" else RuntimeError):
        run(source(tmp_path, count), call, concurrency=1, _budget=budget)
    assert len(calls) <= 6
    assert budget._active == 0 and not budget._waiting
    assert not (tmp_path / "preparation.json").exists()


@pytest.mark.parametrize("exhausted", [False, True])
def test_provider_throttling_backs_off_without_splitting_or_leaking_slots(tmp_path, monkeypatch, exhausted):
    from pydantic_ai.exceptions import ModelHTTPError

    budget = preparation.RequestBudget(1)
    captures, backoffs, events = [], [], []
    def backoff(exc, attempt):
        assert budget._active == 0
        backoffs.append(attempt)
        return 0
    monkeypatch.setattr(preparation, "compute_backoff_delay", backoff)
    async def call(stage, images, context):
        if stage == "capturing":
            captures.append(context)
            assert len(context["pages"]) == 2
            if exhausted or len(captures) == 1:
                raise ModelHTTPError(429, "fake", {"message": "Please try again in 2s"})
        return response(context)

    path = source(tmp_path, 2)
    if exhausted:
        with pytest.raises(preparation.PreparationError):
            run(path, call, concurrency=1, _budget=budget, on_progress=events.append)
        assert len(captures) == 1 + preparation.RATE_LIMIT_MAX_RETRIES
        assert len(backoffs) == preparation.RATE_LIMIT_MAX_RETRIES
        assert not (tmp_path / "preparation.json").exists()
    else:
        assert len(run(path, call, concurrency=1, _budget=budget, on_progress=events.append).pages) == 2
        assert len(captures) == 2 and backoffs == [0]
    assert budget._active == 0 and not budget._waiting
    assert any("Waiting for AI service capacity" in event["message"] for event in events)


@pytest.mark.asyncio
async def test_cancellation_during_throttle_backoff_does_not_retry(tmp_path, monkeypatch):
    from pydantic_ai.exceptions import ModelHTTPError

    waiting = asyncio.Event()
    calls = []
    budget = preparation.RequestBudget(1)
    monkeypatch.setattr(preparation, "compute_backoff_delay", lambda *_: 30)
    async def call(stage, images, context):
        calls.append(context)
        raise ModelHTTPError(429, "fake", {})
    def progress(event):
        if "Waiting for AI service capacity" in event["message"]:
            waiting.set()
    worker = asyncio.create_task(preparation.prepare_document(
        source(tmp_path, 2), None, model_name="fake", _caller=call,
        _budget=budget, on_progress=progress))
    await asyncio.wait_for(waiting.wait(), 3)
    assert budget._active == 0
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, 3)
    assert len(calls) == 1
    assert not (tmp_path / "preparation.json").exists()


@pytest.mark.asyncio
async def test_cancellation_drains_batch_without_single_page_fallback(tmp_path):
    started = asyncio.Event()
    stopped = asyncio.Event()
    calls = []
    budget = preparation.RequestBudget(15)
    async def call(stage, images, context):
        calls.append(context)
        assert len(context["pages"]) == 2
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    task = asyncio.create_task(preparation.prepare_document(
        source(tmp_path, 2), None, model_name="fake", _caller=call, _budget=budget))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel("user stop")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert len(calls) == 1
    assert budget._active == 0 and not budget._waiting
    assert not (tmp_path / "preparation.json").exists()
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert checkpoint["calls"][0]["status"] == "failed"
    assert sorted(checkpoint["calls"][0]["pages"]) == [1, 2]


def test_fifteen_physical_requests_shared_across_document_loops(tmp_path):
    budget = preparation.RequestBudget()
    lock, ready = threading.Lock(), threading.Event()
    active = peak = 0
    async def call(stage, images, context):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 15:
                ready.set()
        try:
            while not ready.is_set():
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.005)
            return response(context)
        finally:
            with lock:
                active -= 1

    paths = [source(tmp_path / str(i), 18) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: run(path, call, _budget=budget, overall_timeout_s=10), paths))
    assert peak == 15
    assert all(len(result.pages) == 18 for result in results)
    assert budget._active == 0 and not budget._waiting


def test_real_agent_uses_batch_capture_and_single_page_verification(tmp_path):
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    calls = []
    def respond(messages, info):
        prompt = next(part for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        context = json.loads(prompt.content[1])
        if "pages" in context:
            assert len(context["pages"]) == 2
        else:
            assert context["page"] in (1, 2)
        calls.append(context)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, response(context))],
                             usage=RequestUsage(input_tokens=30, output_tokens=10))

    prepared = asyncio.run(preparation.prepare_document(
        source(tmp_path, 2), FunctionModel(respond), model_name="offline-function"))
    assert len(prepared.pages) == 2
    assert len(calls) == 3
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    assert sum(c["usage"]["total_tokens"] for c in checkpoint["calls"]) == 120


def test_failed_batch_output_usage_survives_single_page_recovery(tmp_path):
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    model_calls = []
    def respond(messages, info):
        prompt = next(part for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        context = json.loads(prompt.content[1])
        model_calls.append(context)
        result = response(context)
        if "pages" in context and "html" not in context["pages"][0]:
            result = {"pages": "invalid output envelope"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)],
                             usage=RequestUsage(input_tokens=30, output_tokens=10))

    prepared = asyncio.run(preparation.prepare_document(
        source(tmp_path, 2), FunctionModel(respond), model_name="offline-function"))
    assert len(prepared.pages) == 2
    checkpoint = json.loads(next(tmp_path.glob("preparation-checkpoint-*.json")).read_text())
    failed = [c for c in checkpoint["calls"] if c["status"] == "failed"]
    assert len(failed) == 1 and sorted(failed[0]["pages"]) == [1, 2]
    assert failed[0]["usage"]["total_tokens"] == 120
    assert sum(c["usage"]["total_tokens"] for c in checkpoint["calls"]) == len(model_calls) * 40


def test_real_agent_preserves_valid_companion_of_schema_invalid_page(tmp_path):
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    singles = []
    def respond(messages, info):
        prompt = next(part for message in messages for part in message.parts
                      if isinstance(part, UserPromptPart))
        context = json.loads(prompt.content[1])
        result = response(context)
        if "pages" in context and "html" not in context["pages"][0]:
            for page in result["pages"]:
                if page["page"] == 2:
                    page["non_text_regions"] = [{"reason": "scanner_noise", "bbox": [0, 0, 999, 999]}]
        elif "pages" not in context and "html" not in context:
            singles.append(context["page"])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    prepared = asyncio.run(preparation.prepare_document(
        source(tmp_path, 2), FunctionModel(respond), model_name="offline-function"))
    assert singles == [2]
    assert all(page["verified"] for page in prepared.pages)
