"""Stage 2 entrypoint: initialize storage and start Telegram polling."""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from .config import ConfigurationError, load_settings
from .database import initialize
from .telegram_handlers import router
from .startup_recovery import recover_before_workers
from .ready_cleanup import cleanup_terminal_files
from .upload_queue import run_upload_queue
from .queue_maintenance import run_queue_maintenance


async def run_bot(settings) -> None:
    telegram = settings.data["telegram"]
    session = None
    if telegram["api_mode"] == "local":
        session = AiohttpSession(api=TelegramAPIServer.from_base(telegram["api_base_urls"]["local"]))
    bot = Bot(token=settings.token, session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    stop = asyncio.Event()
    logging.info("Worker управляется отдельным постоянным systemd-юнитом")    upload = asyncio.create_task(run_upload_queue(
        stop=stop, bot=bot, db_path=Path("data/bot.sqlite"),
        configured_download_dir=settings.data["storage"]["download_dir"],
        max_bytes=settings.data["telegram"]["max_upload_size_mb"][telegram["api_mode"]] * 1024 * 1024,
        max_attempts=settings.data["telegram_upload_max_attempts"],
        second_attempt_delay_seconds=settings.data["telegram_upload_second_attempt_delay_seconds"],
        third_attempt_delay_seconds=settings.data["telegram_upload_third_attempt_delay_seconds"],
        daily_limit=settings.data["limits"]["daily_download_limit"],
        trusted_users=tuple(settings.data["trusted_users"]),
    ))
    maintenance = asyncio.create_task(run_queue_maintenance(
        stop=stop, db_path=Path("data/bot.sqlite"),
    ))
    polling = asyncio.create_task(dispatcher.start_polling(bot))
    try:
        done, _ = await asyncio.wait(
            (polling, upload, maintenance),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for service, label in ((upload, 'Telegram upload queue'),
                               (maintenance, 'Queue maintenance')):
            if service in done:
                if service.cancelled():
                    raise RuntimeError(f'{label} неожиданно отменён')
                failure = service.exception()
                if failure is not None:
                    raise RuntimeError(f'{label} завершился с ошибкой') from failure
                raise RuntimeError(f'{label} неожиданно завершился')
        await polling
    finally:
        if not polling.done():
            polling.cancel()
            try:
                await polling
            except asyncio.CancelledError:
                pass
        stop.set()
        if not maintenance.done():
            await maintenance
        else:
            try:
                maintenance.result()
            except (Exception, asyncio.CancelledError):
                logging.exception('Ошибка обслуживания очереди при остановке')
        try:
            await asyncio.wait_for(upload, timeout=10)
        except asyncio.TimeoutError:
            upload.cancel()
            try:
                await upload
            except asyncio.CancelledError:
                pass
        finally:
            await bot.session.close()


def main() -> None:
    try:
        settings = load_settings()
        for executable in ("ffmpeg", "ffprobe"):
            if shutil.which(executable) is None:
                raise ConfigurationError(f"{executable}: исполняемый файл не найден")
        cfg = settings.data["logging"]
        Path("logs").mkdir(exist_ok=True)
        logging.basicConfig(
            level=getattr(logging, cfg["level"]),
            handlers=[
                RotatingFileHandler("logs/bot.log", maxBytes=cfg["max_file_size_mb"] * 1024 * 1024, backupCount=cfg["backup_count"], encoding="utf-8"),
                logging.StreamHandler(),
            ],
            format="%(asctime)s %(levelname)s %(message)s",
        )
        db_path = Path("data/bot.sqlite")
        initialize(db_path)
        recovery = recover_before_workers(
            db_path, settings.data["storage"]["download_dir"],
        )
        logging.info(
            "Восстановление: повторно в очереди %d, готовых файлов %d, "
            "недоступных файлов %d, незарегистрированных каталогов %d",
            recovery.requeued, recovery.ready_files,
            recovery.missing_or_unsafe_files, len(recovery.orphan_directories),
        )
        for orphan in recovery.orphan_directories:
            logging.warning("Незарегистрированный каталог (не удалён): %s", orphan)
        cleaned, skipped = cleanup_terminal_files(
            db_path, settings.data["storage"]["download_dir"],
        )
        logging.info("Очистка завершённых задач: удалено %d, пропущено %d", cleaned, skipped)
        logging.info("Запускается Telegram polling (режим %s)", settings.data["telegram"]["api_mode"])
        asyncio.run(run_bot(settings))
    except (ConfigurationError, OSError, RuntimeError) as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        logging.info("Остановка бота")


if __name__ == "__main__":
    main()
