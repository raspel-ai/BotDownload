"""Metadata-only inspection for supported media sites."""
import asyncio

import yt_dlp

from .telegram_handlers import validate_media_url
from .local_tools import yt_dlp_js_options
from .media_variants import available_variants, selected_language
from .network_security import UnsafeNetworkTarget, validate_public_https_target
from .telegram_handlers import ALLOWED_HOSTS


class AnalysisError(Exception):
    pass


def _extract(url: str) -> dict:
    # No generic extractor: only known site extractors may handle the URL.
    opts = {
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "allowed_extractors": ["youtube", "youtube:tab", "Instagram", "TikTok"],
        "socket_timeout": 10,
        "retries": 0,
        "extractor_retries": 0,
        **yt_dlp_js_options(),
    }
    with yt_dlp.YoutubeDL(opts) as downloader:
        info = downloader.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise AnalysisError("Не удалось получить сведения о видео")
    return {
        "title": str(info.get("title") or "Без названия")[:200],
        "duration": info.get("duration"),
        "uploader": str(info.get("uploader") or info.get("channel") or "")[:100],
        "upload_date": str(info.get("upload_date") or ""),
        "view_count": info.get("view_count"),
        "extractor": str(info.get("extractor_key") or ""),
        "thumbnail": str(info.get("thumbnail") or ""),
        "language": selected_language(info),
        "variants": available_variants(info),
    }


async def inspect(url: str, timeout: int = 60, *, network_guard=None) -> dict:
    if not validate_media_url(url):
        raise AnalysisError("Недопустимая ссылка")
    if network_guard is None:
        from .metadata_isolated import inspect_isolated
        from .metadata_process import MetadataProcessError
        try:
            return await inspect_isolated(url, timeout_seconds=timeout)
        except (MetadataProcessError, OSError, asyncio.TimeoutError) as exc:
            raise AnalysisError("Не удалось проанализировать ссылку в изоляции") from exc
    from .metadata_process import MetadataProcessError, inspect_in_subprocess
    try:
        return await inspect_in_subprocess(url, timeout_seconds=timeout, network_guard=network_guard)
    except UnsafeNetworkTarget as exc:
        raise AnalysisError("Небезопасный сетевой адрес") from exc
    except asyncio.TimeoutError as exc:
        raise AnalysisError("Превышено время анализа") from exc
    except (MetadataProcessError, UnsafeNetworkTarget) as exc:
        raise AnalysisError("Не удалось безопасно проанализировать ссылку") from exc
