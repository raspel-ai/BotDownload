"""SQLite -> Telegram sender -> terminal file cleanup, without network."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.download_storage import create_task_directory
from bot.storage import create_task, database
from bot.upload_queue import run_upload_queue


def test_confirmed_mp3_delivery_cleans_staged_file(tmp_path):
    async def scenario():
        root = tmp_path / "downloads"
        root.mkdir()
        db_path = tmp_path / "bot.sqlite"
        with database(db_path) as db:
            task_id, created = create_task(
                db, user_id=123, chat_id=456, message_id=789,
                callback_key="delivery-test", source_url="https://youtu.be/jNQXAC9IVRw",
                media_key="test", variant="MP3",
            )
            assert created
        folder = create_task_directory(root, str(task_id))
        file = folder / "sample.mp3"
        file.write_bytes(b"fake-mp3-payload")
        with database(db_path) as db:
            db.execute(
                "UPDATE tasks SET status='upload_pending',ready_file=? WHERE id=?",
                (str(file), task_id),
            )

        stop = asyncio.Event()
        class FakeBot:
            async def send_audio(self, *, chat_id, audio):
                assert chat_id == 456
                assert isinstance(audio.path, Path)
                assert audio.path == file
                assert file.exists()
                stop.set()
                return SimpleNamespace(audio=SimpleNamespace(file_id="telegram-confirmed-123"))
            async def send_video(self, **kwargs):
                raise AssertionError("MP3 must not be sent as video")

        await asyncio.wait_for(run_upload_queue(
            stop=stop, bot=FakeBot(), db_path=db_path,
            configured_download_dir=str(root), max_bytes=100000,
            max_attempts=2, second_attempt_delay_seconds=0,
            third_attempt_delay_seconds=0, idle_seconds=0.01,
        ), timeout=5)
        with database(db_path) as db:
            status, file_id, ready = db.execute(
                "SELECT status,telegram_file_id,ready_file FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
        assert (status, file_id, ready) == ("completed", "telegram-confirmed-123", None)
        assert not folder.exists()

    asyncio.run(scenario())
