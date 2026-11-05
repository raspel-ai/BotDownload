"""Authenticated media execution within the dedicated worker.

The broker remains the sole SQLite writer. This controller reads a running
lease, uses a private Unix CONNECT gateway, and returns a bounded file path.
"""
import asyncio
from contextlib import closing
import os
import logging
from pathlib import Path
import sqlite3
import stat
import tempfile
from urllib.parse import quote

from .media_egress_gateway import handle_connect
from .media_isolated_runner import execute_isolated
from .worker_reservations import verify_running_lease


def read_media_task(db_path: Path, task_id: int) -> tuple[str, str] | None:
    if not isinstance(db_path, Path) or not db_path.is_absolute():
        return None
    try:
        uri = "file:" + quote(str(db_path), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT source_url,variant FROM tasks WHERE id=? AND status='downloading'",
                (task_id,),
            ).fetchone()
        return (row[0], row[1]) if row else None
    except (OSError, sqlite3.Error):
        return None


async def watch_media_lease(db_path: Path, task_id: int, token: str,
                            cancel: asyncio.Event, *, interval: float = 0.2) -> None:
    """Fail closed when a durable lease is revoked, even without IPC cancel.

    The broker may cancel a task in a separate process; an in-flight IPC
    execute request must not hide that cancellation from the media subprocess.
    """
    while not cancel.is_set():
        if not verify_running_lease(db_path, task_id, token):
            cancel.set()
            return
        try:
            await asyncio.wait_for(cancel.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


class MediaJobController:
    def __init__(self, reservations):
        self.reservations = reservations
        self._busy = False
        self._active_job = None
        self._cancel_event = None

    def cancel(self, task_id: int, token: str) -> bool:
        """Signal cancellation only for the currently executing owned job."""
        if (self._active_job != (task_id, token)
                or self._cancel_event is None):
            return False
        self._cancel_event.set()
        return True

    async def execute(self, db_path: Path, task_id: int, token: str) -> dict:
        rejected = {"version": 1, "status": "rejected", "downloads_enabled": False}
        if (self._busy or not self.reservations.owns(task_id, token)
                or not verify_running_lease(db_path, task_id, token)):
            return rejected
        task = read_media_task(db_path, task_id)
        if task is None:
            return rejected
        root_value = os.environ.get("BOTDOWNLOAD_MEDIA_ROOT", "")
        if not root_value:
            return rejected
        from .download_storage import download_root, create_task_directory, require_free_space
        try:
            root = download_root(root_value)
            require_free_space(root, 256 * 1024 * 1024)
        except Exception:
            logging.exception("media worker preflight failed: task=%d", task_id)
            return rejected
        self._busy = True
        self._active_job = (task_id, token)
        self._cancel_event = asyncio.Event()
        workspace = None
        gateway = None
        gateway_dir = None
        completed = False
        lease_watcher = asyncio.create_task(
            watch_media_lease(db_path, task_id, token, self._cancel_event)
        )
        try:
            workspace = create_task_directory(root, str(task_id))
            gateway_dir = tempfile.TemporaryDirectory(prefix="botdownload-egress-")
            gateway_path = Path(gateway_dir.name) / "gateway.sock"
            gateway = await asyncio.start_unix_server(handle_connect, path=str(gateway_path))
            os.chmod(gateway_path, 0o600)
            async with gateway:
                file = await execute_isolated(
                    task[0], gateway=gateway_path, workspace=workspace,
                    timeout_seconds=120, max_bytes=256 * 1024 * 1024,
                    cancel=self._cancel_event, variant=task[1],
                )
            # Socket is not a downloadable file; unlink before returning.
            if not verify_running_lease(db_path, task_id, token):
                return rejected
            st = file.lstat()
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                    or st.st_nlink != 1 or not 0 < st.st_size <= 256 * 1024 * 1024):
                return rejected
            completed = True
            return {"version": 1, "status": "completed",
                    "downloads_enabled": True, "file": str(file)}
        except asyncio.CancelledError:
            return rejected
        except (OSError, ValueError, RuntimeError, TimeoutError):
            logging.exception("media worker execution failed: task=%d", task_id)
            return rejected
        finally:
            lease_watcher.cancel()
            try:
                await lease_watcher
            except asyncio.CancelledError:
                pass
            if gateway is not None:
                gateway.close()
                await gateway.wait_closed()
            if gateway_dir is not None:
                gateway_dir.cleanup()
            if workspace is not None and not completed:
                from .download_storage import cleanup_task_directory
                try:
                    cleanup_task_directory(root, workspace)
                except OSError:
                    pass
            self._active_job = None
            self._cancel_event = None
            self._busy = False
