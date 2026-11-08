"""Local-only admission of original user URLs (§4 / policy §11)."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

class InvalidURL(ValueError):
    pass

@dataclass(frozen=True)
class ValidatedURL:
    url: str
    platform: str
    resource_id: str

YOUTUBE = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}
INSTAGRAM = {'instagram.com', 'www.instagram.com'}
TIKTOK = {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com'}

# Share tokens and playlist ids are opaque ASCII identifiers, never URLs.
# Normative docs require a finite bound but leave its exact value to implementation.
YOUTUBE_AUX_MAX_LENGTH = 128
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
AUX_ID = re.compile(rf'[A-Za-z0-9_-]{{1,{YOUTUBE_AUX_MAX_LENGTH}}}')


def _youtube_url(parts, short_host: bool) -> ValidatedURL:
    watch = not short_host and parts.path == '/watch'
    allowed = {'v', 'list', 'start_radio'} if watch else {'si'}
    query = {}
    if parts.query:
        # No percent decoding, dropped empty fields or permissive parse_qs defaults.
        for pair in parts.query.split('&'):
            key, separator, value = pair.partition('=')
            if not separator or key not in allowed or key in query or not value:
                raise ValueError
            pattern = VIDEO_ID if key == 'v' else AUX_ID
            if not pattern.fullmatch(value):
                raise ValueError
            query[key] = value
    if watch:
        video_id = query.get('v', '')
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError
        if 'start_radio' in query and (query['start_radio'] != '1' or 'list' not in query):
            raise ValueError
    else:
        pattern = r'/([A-Za-z0-9_-]{11})' if short_host else r'/shorts/([A-Za-z0-9_-]{11})'
        match = re.fullmatch(pattern, parts.path)
        if match is None:
            raise ValueError
        video_id = match[1]
    return ValidatedURL(f'https://www.youtube.com/watch?v={video_id}', 'youtube', video_id)


def validate_url(raw: str) -> ValidatedURL:
    if (type(raw) is not str or not 0 < len(raw) <= 2048 or not raw.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in raw)
            or any(c in raw for c in ('\\', '%', '#'))):
        raise InvalidURL('Отправьте одну HTTPS-ссылку без пробелов и запрещённых параметров.')
    try:
        p = urlsplit(raw)
        host = p.hostname or ''
        if p.scheme != 'https' or p.netloc.lower() != host or not host:
            raise ValueError
        if host in YOUTUBE or host == 'youtu.be':
            if '?' in raw and not p.query:
                raise ValueError
            return _youtube_url(p, short_host=host == 'youtu.be')
        if host in INSTAGRAM:
            m = re.fullmatch(r'/(?:reel|p)/([A-Za-z0-9_-]{1,64})/', p.path)
            platform = 'instagram'
        elif host in TIKTOK:
            m = re.fullmatch(r'/@[A-Za-z0-9_.]{1,24}/video/([0-9]{1,20})', p.path)
            platform = 'tiktok'
        else:
            raise ValueError
        if m is None or p.query or '?' in raw:
            raise ValueError
        return ValidatedURL(f'https://{host}{p.path}', platform, m[1])
    except ValueError:
        raise InvalidURL('Неподдерживаемый адрес видео или параметры ссылки.') from None
