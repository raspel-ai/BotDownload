"""Stage 2 active cancellation: real process groups and media ownership, offline."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import sys
from unittest.mock import patch

import pytest

from bot.media_isolated_runner import execute_isolated
from bot.worker_media import MediaJobController


def test_media_cancel_only_active_owned_job():
    async def scenario():
        controller = MediaJobController(None)
        event = asyncio.Event()
        controller._busy = True
        controller._active_job = (42, "owned-token")
        controller._cancel_event = event
        assert not controller.cancel(43, "owned-token")
        assert not controller.cancel(42, "other-token")
        assert not event.is_set()
        assert controller.cancel(42, "owned-token")
        assert event.is_set()
    asyncio.run(scenario())


def test_active_cancel_kills_isolated_process_group(tmp_path):
    """Fake entry command spawns a real child; cancellation kills the group."""
    async def scenario():
        gateway = tmp_path / "gateway.sock"
        workspace = tmp_path / "work"
        workspace.mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(gateway))
            # Parent and child both sleep; child writes a PID file.
            code = (
                "import subprocess,sys,time,pathlib;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid));"
                "time.sleep(60)"
            )
            child_file = tmp_path / "child.pid"
            event = asyncio.Event()
            with patch("bot.media_isolated_runner.portable_command",
                       return_value=[sys.executable, "-c", code, str(child_file)]):
                task = asyncio.create_task(execute_isolated(
                    "https://www.youtube.com/watch?v=abcdefghijk",
                    gateway=gateway, workspace=workspace,
                    timeout_seconds=10, max_bytes=1024, cancel=event,
                ))
                try:
                    for _ in range(100):
                        if child_file.exists():
                            break
                        await asyncio.sleep(0.02)
                    assert child_file.exists(), "child never started"
                    child_pid = int(child_file.read_text())
                    event.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=5)
                    for _ in range(50):
                        try:
                            state = Path(f"/proc/{child_pid}/stat").read_text().split(") ")[1][0]
                        except FileNotFoundError:
                            break
                        if state == "Z":
                            break
                        await asyncio.sleep(0.02)
                    else:
                        pytest.fail("child survived cancellation")
                finally:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
    asyncio.run(scenario())


def test_ipc_cancel_signals_active_media(tmp_path):
    """Authenticated cancel_reservation reaches the active media controller."""
    import json
    from bot.worker_ipc import handle_connection

    class Reservations:
        def cancel(self, task_id, token):
            return True

    class Media:
        def __init__(self):
            self.calls = []
        def cancel(self, task_id, token):
            self.calls.append((task_id, token))
            return True

    async def scenario():
        media = Media()
        token = "a" * 64
        path = tmp_path / "worker.sock"
        server = await asyncio.start_unix_server(
            lambda reader, writer: handle_connection(
                reader, writer, allowed_uid=os.geteuid(),
                reservations=Reservations(), media=media,
            ), path=str(path),
        )
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(json.dumps({
                "version": 1, "op": "cancel_reservation",
                "task_id": 7, "token": token,
            }).encode() + b"\n")
            await writer.drain()
            response = json.loads(await asyncio.wait_for(reader.readline(), 3))
            assert response["status"] == "accepted"
            assert media.calls == [(7, token)]
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())


def test_live_lease_revocation_stops_media_and_removes_partial_file(tmp_path):
    """Real SQLite lease; active worker observes cancellation without IPC."""
    from bot.storage import database, create_task, change_status, cancel_task
    from bot.worker_leases import offer_job, transition_job
    from bot.worker_reservations import JobReservations
    from bot.worker_media import MediaJobController

    db_path = tmp_path / "test.sqlite"
    media_root = tmp_path / "media"
    media_root.mkdir()
    with database(db_path) as db:
        task_id, _ = create_task(
            db, user_id=1, chat_id=1, message_id=1,
            callback_key="cancel-live-lease", source_url="https://youtu.be/abcdefghijk",
            media_key="test", variant="video",
        )
        assert change_status(db, task_id, "queued", "downloading")
        token = offer_job(db, task_id, ttl_seconds=180)
    reservations = JobReservations()
    assert reservations.accept(db_path, task_id, token)
    with database(db_path) as db:
        assert transition_job(db, task_id, token, "offered", "running")

    async def scenario():
        controller = MediaJobController(reservations)
        started = asyncio.Event()
        async def simulated_isolated(url, *, gateway, workspace,
                                     timeout_seconds, max_bytes, cancel, variant="480p"):
            (workspace / "partial.webm").write_bytes(b"partial download")
            started.set()
            await asyncio.wait_for(cancel.wait(), timeout=5)
            raise asyncio.CancelledError()

        with (
            patch.dict(os.environ, {"BOTDOWNLOAD_MEDIA_ROOT": str(media_root)}),
            patch("bot.worker_media.execute_isolated", simulated_isolated),
            patch("bot.download_storage.require_free_space"),
        ):
            running = asyncio.create_task(controller.execute(db_path, task_id, token))
            await asyncio.wait_for(started.wait(), timeout=5)
            with database(db_path) as db:
                assert cancel_task(db, task_id=task_id, user_id=1)
            result = await asyncio.wait_for(running, timeout=5)
            assert result["status"] == "rejected"
            assert result["downloads_enabled"] is False
            assert not list(media_root.iterdir()), "partial workspace survived cancellation"
            with database(db_path) as db:
                assert db.execute(
                    "SELECT status,ready_file FROM tasks WHERE id=?", (task_id,)
                ).fetchone() == ("cancelled", None)
    asyncio.run(scenario())


def test_lease_watcher_fails_closed_on_missing_lease(tmp_path):
    from bot.worker_media import watch_media_lease
    async def scenario():
        cancel = asyncio.Event()
        await asyncio.wait_for(
            watch_media_lease(tmp_path / "missing.sqlite", 1, "a" * 64, cancel),
            timeout=2,
        )
        assert cancel.is_set()
    asyncio.run(scenario())
