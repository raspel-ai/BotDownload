"""Strict, centralized configuration loading."""
from dataclasses import dataclass
from pathlib import Path
import os

import yaml
from dotenv import load_dotenv


class ConfigurationError(Exception):
    """Configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    data: dict
    token: str


SCHEMA = {
    "telegram": {"api_mode": str, "api_base_urls": {"cloud": str, "local": str}, "max_upload_size_mb": {"cloud": int, "local": int}, "reconnect": {"initial_delay_seconds": int, "max_delay_seconds": int, "backoff_multiplier": int}},
    "limits": {"max_video_duration_seconds": int, "max_audio_duration_seconds": int, "daily_download_limit": int, "max_pending_tasks": int, "queue_wait_timeout_minutes": int},
    "storage": {"unknown_size_fallback_mib": int},
    "rate_limit": {"link_attempts": int, "window_seconds": int, "ban_duration_seconds": int},
    "max_download_attempts": int,
    "telegram_upload_max_attempts": int,
    "telegram_upload_second_attempt_delay_seconds": int,
    "telegram_upload_third_attempt_delay_seconds": int,
    "timeouts": {"analysis_timeout_seconds": int, "download_timeout_seconds": int, "ffmpeg_timeout_seconds": int},
    "backup": {"max_copies": int, "interval_hours": int},
    "logging": {"level": str, "max_file_size_mb": int, "backup_count": int},
    "admin_telegram_id": int,
    "trusted_users": list,
}


def _validate(data, schema, prefix=""):
    if not isinstance(data, dict):
        raise ConfigurationError(f"{prefix or 'config'}: ожидается объект")
    for key in schema:
        path = f"{prefix}.{key}" if prefix else key
        if key not in data:
            raise ConfigurationError(f"{path}: обязательный параметр отсутствует")
        value, expected = data[key], schema[key]
        if isinstance(expected, dict):
            _validate(value, expected, path)
        elif type(value) is not expected:
            raise ConfigurationError(f"{path}: неверный тип")
    unknown = set(data) - set(schema)
    if unknown:
        raise ConfigurationError(f"{prefix or 'config'}: неизвестный параметр {sorted(unknown)[0]}")


def load_settings(root: Path = Path(".")) -> Settings:
    path = root / "config.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError("config.yaml: не удалось прочитать конфигурацию") from exc
    _validate(data, SCHEMA)
    if data["telegram"]["api_mode"] not in ("local", "cloud"):
        raise ConfigurationError("telegram.api_mode: допустимы local или cloud")
    if data["logging"]["level"] not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigurationError("logging.level: недопустимый уровень")
    for key in ("cloud", "local"):
        from urllib.parse import urlsplit
        u = urlsplit(data["telegram"]["api_base_urls"][key])
        if u.scheme != ("https" if key == "cloud" else "http") or not u.hostname or u.username or u.password:
            raise ConfigurationError(f"telegram.api_base_urls.{key}: неверный адрес")
    if any(type(x) is not int or x <= 0 for x in data["trusted_users"]):
        raise ConfigurationError("trusted_users: ожидаются положительные Telegram ID")
    if data["admin_telegram_id"] <= 0:
        raise ConfigurationError("admin_telegram_id: ожидается положительный ID")
    def check_positive(node, schema, prefix=""):
        for key, expected in schema.items():
            value = node[key]
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(expected, dict):
                check_positive(value, expected, path)
            elif expected is int and path not in ("logging.backup_count",):
                if value <= 0:
                    raise ConfigurationError(f"{path}: ожидается положительное число")
    check_positive(data, SCHEMA)
    if data["logging"]["backup_count"] < 0:
        raise ConfigurationError("logging.backup_count: не может быть отрицательным")
    if data["telegram"]["reconnect"]["max_delay_seconds"] < data["telegram"]["reconnect"]["initial_delay_seconds"]:
        raise ConfigurationError("telegram.reconnect: максимальная задержка меньше начальной")
    load_dotenv(root / ".env", override=False)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token or token == "PASTE_YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise ConfigurationError("TELEGRAM_BOT_TOKEN: секрет не задан")
    return Settings(data, token)
