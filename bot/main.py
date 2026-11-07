"""Main bot process: state, dispatch and delivery; never starts media tools."""
import asyncio
import fcntl
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
from .broker import Broker
from .client import WorkerClient
from .config import load_settings,ConfigurationError
from .files import private_dir
from .state import Store,StateUnavailable
from .telegram import Telegram

class Redaction(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r'https?://\S+', '[URL]', record.getMessage())
        record.msg = re.sub(r'\b\d{6,}:[A-Za-z0-9_-]+', '[TOKEN]', record.msg)
        record.args = ()
        # Third-party tracebacks can contain full URLs/secrets; log safe categories only.
        if record.exc_info:
            record.msg += ' ('+record.exc_info[0].__name__+')'
            record.exc_info = None
        return True

async def run(settings):
    config = settings.data
    store = Store()
    telegram = Telegram(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,stop.set)
    try:
        store.initialize(config['backup'])
    except StateUnavailable:
        # A damaged DB must never be silently replaced by a fresh empty DB.
        logging.error('SQLite недоступна; обработка остановлена')
        await telegram.reconnect(stop,lambda:telegram.notify(config['admin_telegram_id'],'Критическая ошибка SQLite. Обработка остановлена; проверьте резервные копии.'))
        await stop.wait()
        await telegram.bot.session.close()
        return
    telegram.on_recovered=lambda:store.notice('telegram_recovered','Соединение с Telegram восстановлено после длительного сбоя.')
    store.notice('startup','BotDownload запущен/перезапущен.')
    broker = Broker(store,WorkerClient(Path(config['worker']['socket'])),config,telegram)
    tasks = [asyncio.create_task(broker.run(stop)),asyncio.create_task(telegram.polling(broker,stop))]
    waiter = asyncio.create_task(stop.wait())
    try:
        done,_ = await asyncio.wait([*tasks,waiter],return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is not waiter:
                t.result()
    finally:
        stop.set()
        tasks[1].cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),timeout=30)
        except TimeoutError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        waiter.cancel()
        await telegram.bot.session.close()

def main():
    os.umask(0o077)
    try:
        settings = load_settings()
        private_dir('logs')
        cfg = settings.data['logging']
        handlers = [RotatingFileHandler('logs/bot.log',maxBytes=cfg['max_file_size_mb']*1024**2,backupCount=cfg['backup_count'],encoding='utf-8'),logging.StreamHandler()]
        for h in handlers:
            h.addFilter(Redaction())
        logging.basicConfig(level=cfg['level'],handlers=handlers,format='%(asctime)s %(levelname)s %(message)s',force=True)
        private_dir('data')
        with Path('data/bot.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Бот уже запущен') from None
            asyncio.run(run(settings))
    except (ConfigurationError,OSError,RuntimeError) as exc:
        print(f'Ошибка запуска: {type(exc).__name__}: {exc}')
        raise SystemExit(1) from None

if __name__ == '__main__':
    main()
