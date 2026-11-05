"""Guard the explicit opt-in Telegram -> media dispatcher wiring."""
from pathlib import Path


def test_main_wires_dispatcher_to_upload_queue():
    source = Path("bot/main.py").read_text()
    assert 'BOTDOWNLOAD_EXPERIMENTAL_MEDIA' in source
    assert "run_media_dispatcher(" in source
    assert "run_upload_queue(" in source
    assert "configured_download_dir=settings.data[\"storage\"][\"download_dir\"]" in source
    assert "media_dispatch.cancel()" in source


def test_telegram_queue_notice_is_not_always_disabled():
    source = Path("bot/telegram_handlers.py").read_text()
    assert "Загрузка поставлена в очередь." in source
    assert "Скачивание пока отключено." in source
