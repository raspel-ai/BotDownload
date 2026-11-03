"""Stage 1 entrypoint: validates prerequisites and initializes storage."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sys

from .config import ConfigurationError, load_settings
from .database import initialize


def main() -> None:
    try:
        settings = load_settings()
        for executable in ("ffmpeg", "ffprobe"):
            if shutil.which(executable) is None:
                raise ConfigurationError(f"{executable}: исполняемый файл не найден")
        if shutil.which("yt-dlp") is None:
            raise ConfigurationError("yt-dlp: исполняемый файл не найден")
        cfg = settings.data["logging"]
        Path("logs").mkdir(exist_ok=True)
        logging.basicConfig(
            level=getattr(logging, cfg["level"]),
            handlers=[RotatingFileHandler("logs/bot.log", maxBytes=cfg["max_file_size_mb"] * 1024 * 1024, backupCount=cfg["backup_count"], encoding="utf-8"), logging.StreamHandler()],
            format="%(asctime)s %(levelname)s %(message)s",
        )
        initialize(Path("data/bot.sqlite"))
        logging.info("Основа проекта инициализирована. Telegram polling будет добавлен на этапе 2.")
    except (ConfigurationError, OSError, RuntimeError) as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
