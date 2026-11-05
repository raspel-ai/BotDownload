"""Namespace entrypoint: configure loopback without host network access."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .media_executor import UnsafeMediaRequest, validate_media_url


def entry_command(url: str, gateway: Path, *, timeout_seconds: int, max_bytes: int, variant: str = '480p') -> list[str]:
    validate_media_url(url)
    if variant not in ("480p", "720p", "1080p", "MP3"):
        raise UnsafeMediaRequest("Неизвестный формат")
    if (not gateway.is_absolute() or not gateway.is_socket()
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
            or type(max_bytes) is not int or not 1 <= max_bytes <= 2**31):
        raise UnsafeMediaRequest("Небезопасная конфигурация namespace")
    unshare = shutil.which("unshare")
    if not unshare or not shutil.which("ip"):
        raise UnsafeMediaRequest("Не установлены unshare/ip")
    return [
        unshare, "--user", "--map-root-user", "--net", "--fork",
        sys.executable, "-m", "bot.media_namespace_entry",
        str(gateway), str(timeout_seconds), str(max_bytes), url, variant,
    ]


def main() -> None:
    if len(sys.argv) not in (5, 6):
        raise SystemExit(2)
    gateway, timeout, max_bytes, url = sys.argv[1:5]
    variant = sys.argv[5] if len(sys.argv) == 6 else "480p"
    if variant not in ("480p", "720p", "1080p", "MP3"):
        raise SystemExit(2)
    # Executed only inside the namespace by entry_command.
    # No network interface is attached; bring up only loopback.
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True, timeout=5)
    from .media_namespace_bridge import main as bridge_main
    args = [
        "--no-config", "--no-playlist", "--no-cache-dir",
        "--no-write-info-json", "--no-write-thumbnail",
        "--no-write-subs", "--no-write-auto-subs",
        "--restrict-filenames", "--max-filesize", max_bytes,
        "--socket-timeout", "10", "--retries", "0",
        "--fragment-retries", "0",
        "--output", "%(id).80s.%(ext)s",
        "--", url,
    ]
    if variant == "MP3":
        args[0:0] = ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "5"]
    else:
        height = variant[:-1]
        args[0:0] = [
            "--format", "bv*[height<=" + height + "]+ba/b[height<=" + height + "]",
            "--merge-output-format", "mp4",
            "--recode-video", "mp4",
        ]
    # Bridge's entrypoint reads argv; no shell and no arbitrary commands.
    sys.argv = ["media_namespace_bridge", gateway, timeout, *args]
    bridge_main()


if __name__ == "__main__":
    main()
