"""Regression tests for actual yt-dlp format arguments."""
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.media_namespace_entry import main


@pytest.mark.parametrize("variant,height", [
    ("480p", "480"), ("720p", "720"), ("1080p", "1080"),
])
def test_video_quality_selects_bounded_height_and_mp4(variant, height):
    argv = ["entry", "/tmp/gateway.sock", "120", "1000000",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw", variant]
    with patch("sys.argv", argv), patch("subprocess.run"), patch(
        "bot.media_namespace_bridge.main"
    ) as bridge:
        main()
        assert bridge.called
        from sys import argv as actual
        assert "--format" in actual
        selector = actual[actual.index("--format") + 1]
        assert "height<=" + height in selector
        assert "--recode-video" in actual
        assert actual[actual.index("--recode-video") + 1] == "mp4"


def test_mp3_uses_ffmpeg_audio_conversion():
    argv = ["entry", "/tmp/gateway.sock", "120", "1000000",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw", "MP3"]
    with patch("sys.argv", argv), patch("subprocess.run"), patch(
        "bot.media_namespace_bridge.main"
    ):
        main()
        from sys import argv as actual
        assert "--extract-audio" in actual
        assert actual[actual.index("--audio-format") + 1] == "mp3"
        assert "--recode-video" not in actual


def test_invalid_format_rejected_before_execution():
    argv = ["entry", "/tmp/gateway.sock", "120", "1000000",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw", "avi"]
    with patch("sys.argv", argv), patch("subprocess.run") as runner:
        with pytest.raises(SystemExit):
            main()
        runner.assert_not_called()
