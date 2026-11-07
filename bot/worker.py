"""Permanent media worker: in-memory execution state, never a SQLite client."""
import asyncio
import fcntl
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import stat
from .client import peer_uid
from .config import load_settings
from .files import private_dir, UnsafeFile
from .isolation import IsolationError
from .media import MediaTools
from .processes import ToolError
from .protocol import MAX_MESSAGE, request, encode

@dataclass
class Execution:
    action: str
    payload: dict
    status: str = 'accepted'
    result: dict | None = None
    error: dict | None = None
    task: asyncio.Task | None = None
    stage: str = ''

class Controller:
    def __init__(self, config, tools=None):
        self.config = config
        self.tools = tools or MediaTools(config)
        self.jobs = {}

    async def execute(self, task_id, job):
        job.status = 'running'
        try:
            if job.action == 'inspect':
                job.result = await self.tools.inspect(task_id, job.payload)
            else:
                job.result = await self.tools.download(task_id, job.payload, lambda s: setattr(job, 'stage', s))
            # Ensure output can actually cross the wire before announcing completion.
            encode({'version': 1, 'request_id': 'x'*64, 'task_id': task_id, 'status': 'completed', 'result': job.result})
            job.status = 'completed'
        except asyncio.CancelledError:
            job.status, job.result = 'cancelled', None
        except (ToolError, IsolationError, UnsafeFile) as exc:
            job.status = 'failed'
            job.error = {'code': getattr(exc, 'code', str(exc) if isinstance(exc,UnsafeFile) else 'isolation_unconfirmed'), 'message': 'Задание не выполнено.', 'permanent': getattr(exc, 'permanent', True)}
        except Exception:
            job.status = 'failed'
            job.error = {'code': 'worker_error', 'message': 'Не удалось обработать источник.', 'permanent': False}

    async def dispatch(self, req):
        task_id, action, payload = req['task_id'], req['action'], req['payload']
        job = self.jobs.get(task_id)
        if action in {'inspect', 'download'}:
            if job is not None and job.action == action:
                if job.payload != payload:
                    return self.answer(req, 'failed', error={'code': 'task_conflict', 'message': 'Несогласованное задание.'})
                return self.snapshot(req, job)
            if job is not None and job.status in {'accepted', 'running'}:
                return self.answer(req, 'failed', error={'code': 'task_busy', 'message': 'Задание уже выполняется.'})
            active = [j for j in self.jobs.values() if j.status in {'accepted', 'running'}]
            if (action == 'download' and any(j.action == 'download' for j in active)) or (action == 'inspect' and sum(j.action == 'inspect' for j in active) >= self.config['worker']['max_inspects']):
                return self.answer(req, 'failed', error={'code': 'worker_busy', 'message': 'Worker занят.', 'permanent': False})
            # A repeated download may only reuse its existing terminal result. A new attempt
            # uses a new execution id allocated durably by the bot (parent request unchanged).
            if len(self.jobs) >= 1024:
                for old_id, old_job in list(self.jobs.items()):
                    if old_job.status not in {'accepted','running'}:
                        del self.jobs[old_id]
                        if len(self.jobs)<1024:
                            break
            job = Execution(action, payload)
            self.jobs[task_id] = job
            job.task = asyncio.create_task(self.execute(task_id, job))
            return self.snapshot(req, job)
        if job is None:
            return self.answer(req, 'not_found')
        if action == 'cancel' and job.status in {'accepted', 'running'}:
            job.task.cancel()
            await job.task  # process-group teardown finishes before acknowledgement
            job.status = 'cancelled'
        return self.snapshot(req, job)

    def snapshot(self, req, job):
        result = job.result
        if job.status == 'running' and job.stage:
            result = {'stage': job.stage}
        return self.answer(req, job.status, result=result, error=job.error)

    @staticmethod
    def answer(req, status, **extra):
        return {'version': 1, 'request_id': req['request_id'], 'task_id': req['task_id'], 'status': status, **{k:v for k,v in extra.items() if v is not None}}

    async def close(self):
        active = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for t in active:
            t.cancel()
        await asyncio.gather(*active, return_exceptions=True)

async def connection(reader, writer, controller):
    try:
        if peer_uid(writer) != os.geteuid():
            return
        async with asyncio.timeout(5):
            req = request(await reader.readline())
            reply = await controller.dispatch(req)
            writer.write(encode(reply))
            await writer.drain()
    except (ValueError, OSError, TimeoutError, TypeError):
        # Invalid envelopes cannot be safely correlated; close without execution.
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

async def serve(config, *, stop=None):
    path = Path(config['worker']['socket'])
    private_dir(path.parent)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise RuntimeError('Небезопасный путь socket')
        try:
            r, w = await asyncio.open_unix_connection(path)
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink(missing_ok=True)
        else:
            w.close()
            await w.wait_closed()
            raise RuntimeError('Worker уже запущен')
    controller = Controller(config)
    server = await asyncio.start_unix_server(lambda r,w: connection(r,w,controller), path=path, limit=MAX_MESSAGE)
    os.chmod(path, 0o600)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        server.close()
        await server.wait_closed()
        await controller.close()
        path.unlink(missing_ok=True)

def main():
    if os.geteuid() == 0:
        raise SystemExit('Worker должен работать без root')
    os.umask(0o077)
    config=load_settings(secrets=False).data
    for binary in ('ffmpeg','ffprobe'):
        import shutil
        if shutil.which(binary) is None:
            raise SystemExit(f'{binary}: обязательная зависимость отсутствует')
    import sys
    if not (Path(sys.executable).parent/'deno').is_file():
        raise SystemExit('deno: обязательная зависимость отсутствует')
    private_dir('data')
    with Path('data/worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Worker уже запущен или выполняется обслуживание') from None
        asyncio.run(serve(config))

if __name__ == '__main__':
    main()
