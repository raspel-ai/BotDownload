"""Pure, strict validation of the *original* user URL (spec §4, policy §1)."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit, parse_qsl


@dataclass(frozen=True)
class ValidatedURL:
    platform: str
    url: str
    resource_type: str
    resource_id: str


class URLValidationError(ValueError):
    pass


_YOUTUBE = frozenset(("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"))
_INSTAGRAM = frozenset(("instagram.com", "www.instagram.com"))
_TIKTOK = frozenset(("tiktok.com", "www.tiktok.com", "m.tiktok.com"))
_YT_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z", re.ASCII)
_IG_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_TT_ID = re.compile(r"[0-9]{1,32}\Z", re.ASCII)
_TT_USER = re.compile(r"[A-Za-z0-9._]{1,24}\Z", re.ASCII)


def validate_url(raw_url: str) -> ValidatedURL:
    """Validate without DNS, redirects or HTTP; reject ambiguous URL spellings."""
    if (not isinstance(raw_url, str) or not 1 <= len(raw_url) <= 2048
            or any(ord(c) <= 32 or ord(c) == 127 for c in raw_url)
            or any(c in raw_url for c in ("\\", "%", "#"))):
        raise URLValidationError("Некорректная ссылка")
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        if (parsed.scheme != "https" or not hostname or not hostname.isascii()
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or "@" in parsed.netloc
                or parsed.netloc != hostname or parsed.fragment):
            raise URLValidationError("Недопустимый адрес")
    except ValueError as exc:
        raise URLValidationError("Некорректный адрес") from exc
    path, query = parsed.path, parsed.query
    if not path or "//" in path or "?" in path:
        raise URLValidationError("Неоднозначный путь")
    if hostname in _YOUTUBE:
        if path != "/watch" or not query:
            raise URLValidationError("Неподдерживаемый путь YouTube")
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
        if len(pairs) != 1 or pairs[0][0] != "v" or not _YT_ID.fullmatch(pairs[0][1]):
            raise URLValidationError("Недопустимые параметры YouTube")
        return ValidatedURL("youtube", raw_url, "video", pairs[0][1])
    if hostname == "youtu.be":
        if query or not _YT_ID.fullmatch(path.removeprefix("/")) or path.count("/") != 1:
            raise URLValidationError("Неподдерживаемая ссылка youtu.be")
        return ValidatedURL("youtube", raw_url, "video", path[1:])
    if hostname in _INSTAGRAM:
        match = re.fullmatch(r"/(reel|p)/([A-Za-z0-9_-]{1,64})/?", path, re.ASCII)
        if query or match is None or not _IG_ID.fullmatch(match[2]):
            raise URLValidationError("Неподдерживаемая ссылка Instagram")
        return ValidatedURL("instagram", raw_url, match[1], match[2])
    if hostname in _TIKTOK:
        match = re.fullmatch(r"/@([A-Za-z0-9._]{1,24})/video/([0-9]{1,32})", path, re.ASCII)
        if query or match is None or not _TT_USER.fullmatch(match[1]) or not _TT_ID.fullmatch(match[2]):
            raise URLValidationError("Неподдерживаемая ссылка TikTok")
        return ValidatedURL("tiktok", raw_url, "video", match[2])
    raise URLValidationError("Домен не поддерживается")
