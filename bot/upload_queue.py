"""Sequential upload queue started by Telegram polling.

Upload attempts and retry deadlines live in SQLite. This worker never deletes
files and must not run alongside another uploader for the same database.
"""
import asyncio
import logging
from pathlib import Path

from .storage import database
from .upload_coordinator import upload_one_task


def next_due_upload(
    db_path: Path, *, daily_limit: int = 3, trusted_users: tuple[int, ...] = (),
) -> int | None:
    """Oldest due task with available UTC quota; never starve other users."""
    from datetime import datetime, timezone
    if type(daily_limit) is not int or daily_limit <= 0:
        raise ValueError("Некорректный дневной лимит")
    utc_day = datetime.now(timezone.utc).date().isoformat()
    with database(db_path) as db:
        rows = db.execute(
            """SELECT id,user_id FROM tasks
            WHERE status='upload_pending'
              AND ready_file IS NOT NULL AND ready_file != ''
              AND (next_upload_at IS NULL OR julianday(next_upload_at) <= julianday('now'))
            ORDER BY created_at,id"""
        ).fetchall()
        for task_id, user_id in rows:
            if user_id in trusted_users:
                return task_id
            used = db.execute(
                "SELECT COUNT(*) FROM daily_usage WHERE user_id=? AND utc_day=?",
                (user_id, utc_day),
            ).fetchone()[0]
            reserved = db.execute(
                "SELECT COUNT(*) FROM delivery_reservations WHERE user_id=? AND utc_day=?",
                (user_id, utc_day),
            ).fetchone()[0]
            if used + reserved < daily_limit:
                return task_id
    return None


async def run_upload_queue(
    *, stop: asyncio.Event, bot, db_path: Path,
    configured_download_dir: str, max_bytes: int, max_attempts: int,
    second_attempt_delay_seconds: int, third_attempt_delay_seconds: int,
    idle_seconds: float = 1.0, project_root: Path = Path("."),
    daily_limit: int = 3, trusted_users: tuple[int, ...] = (),
) -> None:
    """Send one pending file at a time, waiting when no upload is due."""
    if idle_seconds <= 0:
        raise ValueError("idle_seconds должен быть положительным")
    while not stop.is_set():
        task_id = next_due_upload(db_path, daily_limit=daily_limit, trusted_users=trusted_users)
        if task_id is not None:
            try:
                delivered = await upload_one_task(
                    bot=bot, db_path=db_path, task_id=task_id,
                    configured_download_dir=configured_download_dir,
                    max_bytes=max_bytes, max_attempts=max_attempts,
                    second_attempt_delay_seconds=second_attempt_delay_seconds,
                    third_attempt_delay_seconds=third_attempt_delay_seconds,
                    project_root=project_root,
                    daily_limit=daily_limit, trusted_users=trusted_users,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Необработанная ошибка очереди отправки задачи %d", task_id)
                delivered = False
            if delivered:
                from .ready_cleanup import cleanup_terminal_files
                try:
                    cleanup_terminal_files(db_path, configured_download_dir, project_root=project_root)
                except (OSError, ValueError):
                    logging.exception("Не удалось очистить отправленный файл задачи %d", task_id)
                continue
            # Quota exhaustion and failed preflight leave a pending task.
            # Back off instead of spinning on the same task indefinitely.
        try:
            await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
        except asyncio.TimeoutError:
            pass
