"""Cancellation and idempotence tests for worker-owned inspect jobs."""
import asyncio
import pytest

from bot.worker_ipc import WorkerState


def msg(action, task_id=7, payload=None):
    return {"version": 1, "request_id": "req1", "task_id": task_id,
            "action": action, "payload": payload or {}}


@pytest.mark.asyncio
async def test_cancel_running_inspect_waits_for_termination():
    state = WorkerState()
    started = asyncio.Event()
    terminated = asyncio.Event()

    async def fake_inspect(task_id, url):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            terminated.set()

    state.inspect = fake_inspect
    first = asyncio.create_task(state.dispatch(msg("inspect", payload={"url": "https://youtu.be/dQw4w9WgXcQ"})))
    await asyncio.wait_for(started.wait(), 2)
    assert (await state.dispatch(msg("status")))["status"] == "running"
    assert (await state.dispatch(msg("cancel")))["status"] == "cancelled"
    assert terminated.is_set()
    assert (await first)["status"] == "cancelled"
    assert (await state.dispatch(msg("status")))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_inspect_repeated_task_is_not_reexecuted():
    state = WorkerState()
    count = 0

    async def fake_inspect(task_id, url):
        nonlocal count
        count += 1
        return {"title": "example"}

    state.inspect = fake_inspect
    request = msg("inspect", payload={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert (await state.dispatch(request))["status"] == "completed"
    assert (await state.dispatch(request))["status"] == "completed"
    assert count == 1
