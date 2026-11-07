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

def validate_url(raw: str) -> ValidatedURL:
    if (type(raw) is not str or not 0 < len(raw) <= 2048 or not raw.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in raw)
            or any(c in raw for c in ('\\', '%', '#'))):
        raise InvalidURL('Отправьте одну HTTPS-ссылку без пробелов и дополнительных параметров.')
    try:
        p = urlsplit(raw)
        host = p.hostname or ''
        if p.scheme != 'https' or p.netloc.lower() != host or not host:
            raise ValueError
        if host in YOUTUBE:
            m = re.fullmatch(r'v=([A-Za-z0-9_-]{11})', p.query)
            if p.path != '/watch' or m is None:
                raise ValueError
            return ValidatedURL(f'https://{host}/watch?v={m[1]}', 'youtube', m[1])
        if host == 'youtu.be':
            m = re.fullmatch(r'/([A-Za-z0-9_-]{11})', p.path)
            platform = 'youtube'
        elif host in INSTAGRAM:
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
