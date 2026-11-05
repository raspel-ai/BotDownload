"""Regression tests for actual yt-dlp format arguments (portable launcher)."""
import pytest

from bot.media_namespace_entry import download_args


@pytest.mark.parametrize("variant,height", [
    ("480p", "480"), ("720p", "720"), ("1080p", "1080"),
])
def test_video_quality_selects_bounded_height_and_mp4(variant, height):
    args = download_args("https://www.youtube.com/watch?v=jNQXAC9IVRw", "1000000", variant)
    assert "--format" in args
    selector = args[args.index("--format") + 1]
    assert "height<=" + height in selector
    assert args[args.index("--recode-video") + 1] == "mp4"


def test_mp3_uses_ffmpeg_audio_conversion():
    args = download_args("https://www.youtube.com/watch?v=jNQXAC9IVRw", "1000000", "MP3")
    assert "--extract-audio" in args
    assert args[args.index("--audio-format") + 1] == "mp3"
    assert "--recode-video" not in args


def test_invalid_format_rejected_before_execution():
    with pytest.raises(SystemExit):
        download_args("https://www.youtube.com/watch?v=jNQXAC9IVRw", "1000000", "avi")
