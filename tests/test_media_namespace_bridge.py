"""Security regression tests for portable media bridge and launcher.

No external network access or yt-dlp execution.
"""
import asyncio
import socket

import pytest

from bot.media_namespace_bridge import serve_bridge
from bot.media_isolated_runner import portable_command


def test_portable_command_is_explicit_and_no_shell(tmp_path):
    gateway = tmp_path / "egress.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(gateway))
        cmd = portable_command(
            "https://www.youtube.com/watch?v=abcdefghijk",
            gateway, 30, 1024, "480p",
        )
    assert cmd[1:3] == ["-m", "bot.media_namespace_bridge"]
    assert "unshare" not in cmd
    assert "--format" in cmd
    assert "--" in cmd


def test_bridge_forwards_bytes_to_unix_gateway(tmp_path):
    async def scenario():
        path = tmp_path / "egress.sock"
        async def gateway(reader, writer):
            data = await reader.read(128)
            writer.write(data[::-1])
            await writer.drain()
            writer.close()
        upstream = await asyncio.start_unix_server(gateway, path=str(path))
        bridge, port = await serve_bridge(path)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"test")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(4), 2) == b"tset"
            writer.close()
            await writer.wait_closed()
        finally:
            bridge.close()
            upstream.close()
            await bridge.wait_closed()
            await upstream.wait_closed()
    asyncio.run(scenario())


def test_bridge_fails_closed_without_gateway(tmp_path):
    async def scenario():
        with pytest.raises(ValueError):
            await serve_bridge(tmp_path / "missing.sock")
    asyncio.run(scenario())
