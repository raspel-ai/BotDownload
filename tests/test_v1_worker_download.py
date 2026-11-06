"""Download IPC lifecycle tests without external media requests."""
import asyncio
import pytest

from bot.worker_ipc import WorkerState


def message(action, task_id=33, payload=None):
    return {"version": 1, "request_id": "test33", "task_id": task_id,
            "action": action, "payload": payload or {}}


@pytest.mark.asyncio
async def test_download_completion_and_status():
    state = WorkerState()
    calls = []

    async def fake_download(task_id, url, variant):
        calls.append((task_id, variant))
        return {"file": "/controlled/output.mp4", "bytes": 100, "duration": 10}

    state.download = fake_download
    request = message("download", payload={"url": "https://youtu.be/dQw4w9WgXcQ",
                                            "variant": "720p"})
    result = await state.dispatch(request)
    assert result["status"] == "completed"
    assert result["result"]["bytes"] == 100
    assert (await state.dispatch(message("status")))["status"] == "completed"
    assert (await state.dispatch(request))["status"] == "completed"
    assert calls == [(33, "720p")]


@pytest.mark.asyncio
async def test_cancel_download_waits_for_worker_exit():
    state = WorkerState()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def fake_download(task_id, url, variant):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    state.download = fake_download
    job = asyncio.create_task(state.dispatch(message(
        "download", payload={"url": "https://youtu.be/dQw4w9WgXcQ", "variant": "MP3"})))
    await asyncio.wait_for(started.wait(), 2)
    assert (await state.dispatch(message("cancel")))["status"] == "cancelled"
    assert stopped.is_set()
    assert (await job)["status"] == "cancelled"
