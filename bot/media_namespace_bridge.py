"""Network namespace transport for a Unix-only outbound CONNECT gateway.

The caller runs this module under unshare --user --map-root-user --net.
Only loopback TCP is exposed inside that namespace; the upstream gateway
is reached through an inherited filesystem Unix socket.
"""
import asyncio
import os
import resource
import signal
from pathlib import Path
import socket
import sys


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, ConnectionError):
        pass
    finally:
        writer.close()


async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, gateway: Path) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(gateway)), timeout=5,
        )
    except (OSError, asyncio.TimeoutError):
        writer.close()
        return
    a = asyncio.create_task(_pipe(reader, upstream_writer))
    b = asyncio.create_task(_pipe(upstream_reader, writer))
    try:
        _, pending = await asyncio.wait((a, b), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        upstream_writer.close()
        writer.close()


async def serve_bridge(gateway: Path) -> tuple[asyncio.AbstractServer, int]:
    """Return loopback-only listener and its ephemeral port."""
    if not gateway.is_absolute() or not gateway.is_socket():
        raise ValueError("Gateway socket unavailable")
    server = await asyncio.start_server(
        lambda r, w: _forward(r, w, gateway), host="127.0.0.1", port=0,
    )
    return server, server.sockets[0].getsockname()[1]


async def run_download(gateway: Path, args: list[str], *, timeout: float) -> int:
    """Start yt-dlp only after local proxy is listening; fail closed."""
    if not args or not 0 < timeout <= 3600:
        raise ValueError("Invalid download invocation")
    server, port = await serve_bridge(gateway)
    try:
        env = dict(os.environ)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                     "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
            env.pop(name, None)
        # yt-dlp's explicit --proxy applies to extractor and media requests.
        env["NO_PROXY"] = ""
        env["no_proxy"] = ""
        proxy = f"http://127.0.0.1:{port}"
        def limits():
            os.umask(0o077)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (int(timeout) + 2, int(timeout) + 2))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "yt_dlp", "--proxy", proxy, *args,
            env=env, preexec_fn=limits, start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=None,
        )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except BaseException:
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            raise
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(2)
    gateway = Path(sys.argv[1])
    timeout = float(sys.argv[2])
    # Portable v1: no user/network namespace requirement.
    if not gateway.is_absolute() or not gateway.is_socket():
        raise SystemExit(2)
    asyncio.run(_main(gateway, sys.argv[3:], timeout))


async def _main(gateway: Path, args: list[str], timeout: float) -> None:
    # Loopback is brought up by the namespace launcher before this module.
    try:
        status = await run_download(gateway, args, timeout=timeout)
    except asyncio.TimeoutError:
        # Dedicated exit status: outer runner must not confuse a real
        # resource timeout with an extractor/network failure.
        raise SystemExit(124)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
