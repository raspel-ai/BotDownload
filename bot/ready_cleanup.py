"""Conservative cleanup of staged files belonging to terminal tasks.

Only a private task directory containing exactly its registered regular file
may be removed. Ambiguous paths and unexpected directory contents are left
untouched for manual inspection.
"""
import os
from pathlib import Path
import stat

from .download_storage import download_root
from .ready_recovery import valid_ready_file
from .storage import database

_TERMINAL = ("completed", "cancelled", "failed")


def cleanup_terminal_files(db_path: Path, configured_download_dir: str, *,
                           project_root: Path = Path(".")) -> tuple[int, int]:
    """Return (cleaned, skipped). Only terminal tasks are eligible."""
    root = download_root(configured_download_dir, project_root)
    cleaned = skipped = 0
    with database(db_path) as db:
        rows = db.execute(
            """SELECT id,status,ready_file FROM tasks
            WHERE status IN ('completed','cancelled','failed')
            AND ready_file IS NOT NULL"""
        ).fetchall()
        for task_id, status, stored_path in rows:
            if not valid_ready_file(root, task_id, stored_path):
                skipped += 1
                continue
            path = Path(stored_path)
            folder = path.parent
            try:
                folder_stat = folder.lstat()
                if (not stat.S_ISDIR(folder_stat.st_mode)
                        or folder_stat.st_uid != os.getuid()
                        or folder_stat.st_mode & 0o077
                        or list(folder.iterdir()) != [path]):
                    skipped += 1
                    continue
                path.unlink()
                folder.rmdir()
            except OSError:
                skipped += 1
                continue
            db.execute(
                """UPDATE tasks SET ready_file=NULL WHERE id=? AND status=?
                AND ready_file=?""",
                (task_id, status, stored_path),
            )
            cleaned += 1
    return cleaned, skipped
