"""Single validated configuration; worker never loads .env."""
from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlsplit
import yaml
from dotenv import load_dotenv
class ConfigurationError(ValueError):
    pass
SCHEMA = {
 'telegram': {'api_mode': str, 'api_base_urls': {'cloud': str, 'local': str}, 'max_upload_size_mb': {'cloud': int, 'local': int}, 'reconnect': {'initial_delay_seconds': int, 'max_delay_seconds': int, 'backoff_multiplier': int}},
 'limits': {'max_video_duration_seconds': int, 'max_audio_duration_seconds': int, 'daily_download_limit': int, 'max_pending_tasks': int, 'queue_wait_timeout_minutes': int},
 'storage': {'unknown_size_fallback_mib': int, 'keep_downloaded_files': bool, 'files_dir': str},
 'rate_limit': {'link_attempts': int, 'window_seconds': int, 'ban_duration_seconds': int},
 'max_download_attempts': int, 'telegram_upload_max_attempts': int,
 'telegram_upload_second_attempt_delay_seconds': int, 'telegram_upload_third_attempt_delay_seconds': int,
 'timeouts': {'analysis_timeout_seconds': int, 'download_timeout_seconds': int, 'ffmpeg_timeout_seconds': int},
 'backup': {'max_copies': int, 'interval_hours': int},
 'logging': {'level': str, 'max_file_size_mb': int, 'backup_count': int},
 'admin_telegram_id': int, 'trusted_users': list,
 'worker': {'socket': str, 'isolation_confirmation': str, 'max_inspects': int},
}
@dataclass(frozen=True)
class Settings:
    data: dict
    token: str = ''

def validate(data, schema=SCHEMA, prefix=''):
    if type(data) is not dict or set(data) != set(schema):
        raise ConfigurationError(f'{prefix or "config"}: неверный набор параметров')
    for k, typ in schema.items():
        p = f'{prefix}.{k}' if prefix else k
        v = data[k]
        if isinstance(typ, dict):
            validate(v, typ, p)
        elif type(v) is not typ:
            raise ConfigurationError(f'{p}: неверный тип')
        elif typ is int and v <= 0:
            raise ConfigurationError(f'{p}: требуется положительное число')
    if prefix:
        return
    t = data['telegram']
    if t['api_mode'] not in {'cloud', 'local'}:
        raise ConfigurationError('telegram.api_mode: только local/cloud')
    for mode, address in t['api_base_urls'].items():
        try:
            u = urlsplit(address)
            if not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in {'', '/'}:
                raise ValueError
            if mode == 'cloud' and address.rstrip('/') != 'https://api.telegram.org':
                raise ValueError
            if mode == 'local' and u.scheme not in {'http', 'https'}:
                raise ValueError
            _ = u.port
        except ValueError:
            raise ConfigurationError(f'telegram.api_base_urls.{mode}: неверный адрес') from None
    if data['logging']['level'] not in {'DEBUG', 'INFO', 'WARNING', 'ERROR'}:
        raise ConfigurationError('logging.level: неверный уровень')
    if any(type(x) is not int or x <= 0 for x in data['trusted_users']):
        raise ConfigurationError('trusted_users: требуются положительные ID')
    for k in ('socket', 'isolation_confirmation'):
        if not Path(data['worker'][k]).is_absolute() or any(ord(c) < 32 for c in data['worker'][k]):
            raise ConfigurationError(f'worker.{k}: требуется абсолютный путь')
    p = Path(data['storage']['files_dir'])
    if p.is_absolute() or '..' in p.parts or p == Path('.') or p.parts[0] in {'data', 'tmp', 'logs', 'backups', 'work'}:
        raise ConfigurationError('storage.files_dir: требуется отдельный относительный каталог')
    if t['reconnect']['max_delay_seconds'] < t['reconnect']['initial_delay_seconds']:
        raise ConfigurationError('telegram.reconnect: неверные задержки')

def load_settings(root=Path('.'), *, secrets=True):
    try:
        data = yaml.safe_load((root / 'config.yaml').read_text())
    except (OSError, yaml.YAMLError):
        raise ConfigurationError('config.yaml: ошибка чтения') from None
    validate(data)
    token = ''
    if secrets:
        load_dotenv(root / '.env', override=False)
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        if not token or token.startswith('PASTE_'):
            raise ConfigurationError('TELEGRAM_BOT_TOKEN: секрет не задан')
    return Settings(data, token)
