"""Ensure confirmed Telegram deliveries trigger staged-file cleanup."""
from pathlib import Path


def test_upload_queue_cleans_only_after_confirmed_delivery():
    source = Path("bot/upload_queue.py").read_text(encoding="utf-8")
    assert "if delivered:" in source
    assert "cleanup_terminal_files(db_path, configured_download_dir" in source
    assert source.index("if delivered:") < source.index("cleanup_terminal_files(db_path, configured_download_dir")
