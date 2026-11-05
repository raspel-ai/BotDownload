"""Portable metadata inspection through the existing Unix CONNECT gateway."""
import asyncio
import os
from pathlib import Path
import signal
import sys

from .media_namespace_bridge import serve_bridge
from .telegram_handlers import validate_media_url


async def run(gateway: Path, url: str, *, timeout: float = 55) -> int:
    if not validate_media_url(url):
        return 2
    server, port = await serve_bridge(gateway)
    try:
        env = dict(os.environ)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                    "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
            env.pop(key, None)
        env["HTTPS_PROXY"] = "http://127.0.0.1:" + str(port)
        env["HTTP_PROXY"] = env["HTTPS_PROXY"]
        env["NO_PROXY"] = ""
        env["no_proxy"] = ""
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "bot.metadata_worker", url,
            env=env, stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except BaseException:
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await proc.wait()
            raise
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    gateway, url = Path(sys.argv[1]), sys.argv[2]
    if not gateway.is_absolute() or not gateway.is_socket():
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(gateway, url)))


if __name__ == "__main__":
    main()
