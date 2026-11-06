"""Metadata-only inspection for supported media sites."""
import asyncio

import yt_dlp

from .url_validation import validate_url, URLValidationError
from .local_tools import yt_dlp_js_options
from .media_variants import available_variants, selected_language
from .network_security import UnsafeNetworkTarget, validate_public_https_target


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
    """Inspect only via the persistent protected worker; never locally."""
    from .worker_client import request, IPCError
    from .url_validation import validate_url, URLValidationError
    try:
        validate_url(url)
        import secrets
        task_id = secrets.randbelow(2**63 - 1) + 1
        response = await request("inspect", task_id, {"url": url}, timeout=timeout + 10)
    except (URLValidationError, IPCError) as exc:
        raise AnalysisError("Защищённый анализ недоступен") from exc
    if response["status"] != "completed" or not isinstance(response.get("result"), dict):
        raise AnalysisError("Не удалось получить метаданные")
    return response["result"]
