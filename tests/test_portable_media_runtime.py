"""Regression coverage for the ordinary-VPS execution path."""
import inspect
from pathlib import Path
from unittest.mock import patch

from bot.media_namespace_entry import download_args
from bot.media_isolated_runner import execute_isolated
from bot.media_namespace_bridge import main as bridge_main


def test_video_arguments_have_mp4_conversion_and_quality_limit():
    for variant, height in (("480p", 480), ("720p", 720), ("1080p", 1080)):
        args = download_args("https://youtu.be/jNQXAC9IVRw", "50000000", variant)
        assert "--recode-video" in args
        assert args[args.index("--recode-video") + 1] == "mp4"
        assert f"height<={height}" in args[args.index("--format") + 1]


def test_audio_arguments_convert_to_mp3():
    args = download_args("https://youtu.be/jNQXAC9IVRw", "50000000", "MP3")
    assert args[args.index("--audio-format") + 1] == "mp3"
    assert "--extract-audio" in args


def test_media_runner_uses_portable_bridge_not_unshare():
    source = inspect.getsource(execute_isolated)
    assert '"bot.media_namespace_bridge"' in source
    assert '"unshare"' not in source


def test_bridge_rejects_missing_gateway_without_namespaces(tmp_path):
    with patch("sys.argv", ["bridge", str(tmp_path / "missing.sock"), "10", "--help"]):
        try:
            bridge_main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Missing gateway was accepted")
