"""Child-process entry point for metadata extraction.

This process must run behind enforced network egress policy before Telegram
is switched to it. Stdout is reserved for one JSON object.
"""
import json
import sys

from .metadata import _extract
from .url_validation import validate_url, URLValidationError


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    try:
        validate_url(args[0])
    except URLValidationError:
        return 2
    try:
        # Repeat literal URL validation; DNS may legitimately return Fake-IP.
        result = _extract(args[0])
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > 262144:
            return 3
        sys.stdout.write(payload)
        sys.stdout.flush()
        return 0
    except Exception:
        # Do not leak URL tokens, yt-dlp diagnostics, or internal paths.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
