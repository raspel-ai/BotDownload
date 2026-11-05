"""Bounded metadata inspection with the same network isolation as downloads."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .media_egress_gateway import handle_connect
from .metadata_process import MetadataProcessError, run_json_subprocess
from .telegram_handlers import validate_media_url


async def inspect_isolated(url: str, *, timeout_seconds: int = 60) -> dict:
    if not validate_media_url(url):
        raise MetadataProcessError("Недопустимая ссылка")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
        raise MetadataProcessError("Недопустимый тайм-аут")
    with tempfile.TemporaryDirectory(prefix="botdownload-metadata-") as temp:
        socket_path = Path(temp) / "gateway.sock"
        server = await asyncio.start_unix_server(handle_connect, path=str(socket_path))
        os.chmod(socket_path, 0o600)
        try:
            async with server:
                return await run_json_subprocess(
                    [sys.executable, "-m", "bot.metadata_proxy_entry",
                     str(socket_path), url],
                    cwd=Path(__file__).resolve().parent.parent,
                    timeout_seconds=timeout_seconds,
                )
        finally:
            server.close()
            await server.wait_closed()
