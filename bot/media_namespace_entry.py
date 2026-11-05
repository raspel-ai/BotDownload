"""Build yt-dlp arguments for the portable media launcher."""



def download_args(url: str, max_bytes: str, variant: str) -> list[str]:
    if variant not in ("480p", "720p", "1080p", "MP3"):
        raise SystemExit(2)
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
    return args

