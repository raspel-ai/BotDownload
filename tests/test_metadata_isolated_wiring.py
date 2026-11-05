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


def test_namespace_launcher_is_required():
    from bot.metadata_isolated import inspect_isolated
    from bot.metadata_process import MetadataProcessError
    with patch("bot.metadata_isolated.shutil.which", return_value=None):
        try:
            asyncio.run(inspect_isolated(URL))
        except MetadataProcessError:
            pass
        else:
            raise AssertionError("metadata ran without network isolation")


def test_metadata_gateway_supports_provider_hosts():
    from bot.media_egress_gateway import permitted_host
    assert permitted_host("www.instagram.com")
    assert permitted_host("www.tiktok.com")
    assert not permitted_host("evil.example")
