"""Worker-only yt-dlp/FFmpeg entry point; invoked in a supervised process group."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .url_validation import validate_url, URLValidationError

FORMATS = {"480p", "720p", "1080p", "video", "audio", "MP3"}
MAX_BYTES = 2_000_000_000
MAX_DURATION = 7200


def download(url: str, variant: str, workspace: Path) -> dict:
    validate_url(url)
    if variant not in FORMATS or workspace.is_symlink() or not workspace.is_dir():
        raise ValueError("Invalid download parameters")
    import yt_dlp
    audio = variant in ("audio", "MP3")
    height = {"480p": 480, "720p": 720, "1080p": 1080}.get(variant, 1080)
    opts = {
        "outtmpl": str(workspace / "media.%(ext)s"),
        "noplaylist": True,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
        "max_filesize": MAX_BYTES,
        "format": "bestaudio/best" if audio else
                  f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
        "merge_output_format": "mp4",
        "postprocessors": ([{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                              "preferredquality": "0"}] if audio else
                           [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    suffix = ".mp3" if audio else ".mp4"
    files = [p for p in workspace.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == suffix]
    if len(files) != 1:
        raise RuntimeError("Expected exactly one output")
    file = files[0]
    st = file.lstat()
    if st.st_nlink != 1 or st.st_uid != os.getuid() or not 0 < st.st_size <= MAX_BYTES:
        raise RuntimeError("Invalid output")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(file)], capture_output=True, timeout=30, check=True,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    if not 0 < duration <= MAX_DURATION:
        raise RuntimeError("Duration exceeds limit")
    return {"file": str(file), "bytes": st.st_size, "duration": duration,
            "kind": "audio" if audio else "video"}


def main():
    if len(sys.argv) != 4:
        return 2
    url, variant, directory = sys.argv[1:]
    try:
        result = download(url, variant, Path(directory))
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
