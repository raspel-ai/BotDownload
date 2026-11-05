"""Portable subprocess lifecycle regressions."""
import asyncio
from pathlib import Path
from unittest.mock import patch

from bot.metadata_namespace_entry import run


def test_metadata_invalid_url_does_not_open_proxy():
    with patch("bot.metadata_namespace_entry.serve_bridge") as bridge:
        assert asyncio.run(run(Path("/tmp/unused.sock"), "http://localhost")) == 2
        bridge.assert_not_called()


def test_metadata_entry_no_longer_needs_ip_command():
    from bot import metadata_namespace_entry
    import inspect
    source = inspect.getsource(metadata_namespace_entry.main)
    assert "ip" not in source
    assert "subprocess.run" not in source


def test_download_runner_starts_own_process_session():
    from bot import media_namespace_bridge
    import inspect
    source = inspect.getsource(media_namespace_bridge.run_download)
    assert "start_new_session=True" in source
    assert "os.killpg" in source
