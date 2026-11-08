"""Worker-only bounded subprocesses; cancellation always reaps the process group."""
import asyncio
import os
import signal

class ToolError(RuntimeError):
    def __init__(self, code='tool_error', permanent=False):
        self.code, self.permanent = code, permanent
        super().__init__(code)

async def run(args, *, timeout, cwd=None, output_limit=1024*1024, progress=None, processing_timeout=None):
    spawn=asyncio.create_task(asyncio.create_subprocess_exec(*args,cwd=cwd,stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,
        env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','HOME','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','http_proxy','https_proxy','no_proxy'}}))
    try:
        p=await asyncio.shield(spawn)
    except asyncio.CancelledError:
        p=await spawn
        try:os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        await p.communicate()
        raise
    timer=None
    processing=False
    async def bounded(stream, keep):
        nonlocal processing
        data = bytearray()
        while chunk := await stream.read(8192):
            if len(data) + len(chunk) > output_limit:
                raise ToolError('tool_output_limit')
            data.extend(chunk)
            if keep and processing_timeout is not None and not processing and b'BOTDOWNLOAD_PROCESSING' in data:
                processing=True
                if timer is not None:
                    timer.reschedule(asyncio.get_running_loop().time()+processing_timeout)
            if progress:
                progress(bytes(data))
        return bytes(data) if keep else bytes(data[-8192:])
    readers = [asyncio.create_task(bounded(p.stdout, True)), asyncio.create_task(bounded(p.stderr, False))]
    try:
        async with asyncio.timeout(timeout) as timer:
            out, err = await asyncio.gather(*readers)
            await p.wait()
        if p.returncode:
            text = err.decode(errors='replace').lower()
            permanent = any(x in text for x in ('not available', 'private video', 'sign in', 'login required', 'unsupported url', 'no space left', 'source_unavailable', 'disk_full', 'requested format is not available', 'video unavailable', 'copyright'))
            code = 'disk_full' if 'no space left' in text or 'disk_full' in text else 'source_unavailable' if permanent else 'tool_error'
            raise ToolError(code, permanent)
        return out
    except TimeoutError:
        raise ToolError('timeout') from None
    finally:
        # Also kill grandchildren after a leader exits: pgid remains valid.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for t in readers:
            if not t.done():
                t.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await asyncio.shield(p.wait())
