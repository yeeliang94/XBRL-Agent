"""Cross-thread cancellation for suite-child and background run tasks."""
from __future__ import annotations

import asyncio
import threading

import task_registry


def test_cancel_all_dispatches_to_the_tasks_owning_event_loop():
    session_id = "suite-child-cross-loop"
    registered = threading.Event()
    cancelled = threading.Event()
    finished = threading.Event()
    observed: dict[str, object] = {}

    def worker() -> None:
        async def run() -> None:
            class ThreadRecordingFuture(asyncio.Future):
                def cancel(self, msg=None):
                    observed["waiter_cancel_thread"] = threading.get_ident()
                    return super().cancel(msg)

            task = asyncio.current_task()
            assert task is not None
            observed["owner_thread"] = threading.get_ident()
            task_registry.register(session_id, "SOFP", task)
            registered.set()
            try:
                await ThreadRecordingFuture()
            except asyncio.CancelledError as exc:
                observed["user_abort"] = task_registry.is_user_abort(exc)
                cancelled.set()
            finally:
                task_registry.unregister(session_id, "SOFP")

        loop = asyncio.new_event_loop()
        loop.set_debug(True)
        original_call_soon_threadsafe = loop.call_soon_threadsafe

        def tracked_call_soon_threadsafe(callback, *args, **kwargs):
            observed["threadsafe_dispatch_thread"] = threading.get_ident()
            return original_call_soon_threadsafe(callback, *args, **kwargs)

        loop.call_soon_threadsafe = tracked_call_soon_threadsafe
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert registered.wait(timeout=2)

    assert task_registry.cancel_all(session_id) == 1
    assert cancelled.wait(timeout=2)
    assert finished.wait(timeout=2)
    thread.join(timeout=2)
    assert observed["user_abort"] is True
    assert observed["threadsafe_dispatch_thread"] == threading.get_ident()
    assert observed["waiter_cancel_thread"] == observed["owner_thread"]
    assert task_registry.get_task(session_id, "SOFP") is None
