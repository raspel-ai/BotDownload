"""Metadata inspection is wired to a namespace, never direct network."""
import asyncio
from unittest.mock import AsyncMock, patch

from bot.metadata import inspect


URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_inspect_uses_isolated_runner_by_default():
    result = {"title": "test", "variants": []}
    with patch("bot.metadata_isolated.inspect_isolated", new_callable=AsyncMock) as runner:
        runner.return_value = result
        assert asyncio.run(inspect(URL, timeout=30)) == result
        runner.assert_awaited_once_with(URL, timeout_seconds=30)


def test_portable_launcher_does_not_require_unshare():
    from bot.metadata_isolated import inspect_isolated
    with patch("bot.metadata_isolated.shutil.which", return_value=None), patch(
        "bot.metadata_isolated.run_json_subprocess", new_callable=AsyncMock
    ) as subprocess_call:
        subprocess_call.return_value = {"title": "ok"}
        assert asyncio.run(inspect_isolated(URL)) == {"title": "ok"}
        argv = subprocess_call.await_args.args[0]
        assert "bot.metadata_proxy_entry" in argv
        assert "unshare" not in argv


def test_metadata_gateway_supports_provider_hosts():
    from bot.media_egress_gateway import permitted_host
    assert permitted_host("www.instagram.com")
    assert permitted_host("www.tiktok.com")
    assert not permitted_host("evil.example")
