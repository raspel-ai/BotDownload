"""Fail-closed worker admission; confirmation is supplied only by the operator."""
import json
import hashlib
import os
from pathlib import Path
import stat

def unit_digest(roots=None):
    # Include persistent/runtime control overrides and inherited service drop-ins.
    roots = roots or ('/etc/systemd/system', '/run/systemd/system',
                      '/etc/systemd/system.control', '/run/systemd/system.control',
                      '/usr/local/lib/systemd/system', '/usr/lib/systemd/system')
    digest = hashlib.sha256()
    for base in roots:
        root = Path(base)
        files = [root / 'tgbot-worker.service']
        for directory in ('service.d', 'tgbot-.service.d', 'tgbot-worker.service.d'):
            files.extend(sorted((root / directory).glob('*.conf')))
        for p in files:
            if p.exists():
                digest.update(str(p).encode())
                digest.update(p.read_bytes())
    return digest.hexdigest()

class IsolationError(RuntimeError):
    pass

def require_isolation(config):
    if os.geteuid() == 0:
        raise IsolationError('worker_must_be_unprivileged')
    cgroup = Path('/proc/self/cgroup').read_text()
    if not any(line.endswith('/tgbot-worker.service') for line in cgroup.splitlines()):
        raise IsolationError('worker_outside_permanent_unit')
    path = Path(config['worker']['isolation_confirmation'])
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError
        record = json.loads(path.read_text())
        if record != {'unit': 'tgbot-worker.service', 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'loopback_denied': True, 'dns_https_ok': True, 'children_confined': True, 'unit_sha256': unit_digest()}:
            raise ValueError
    except (OSError, ValueError):
        raise IsolationError('worker_isolation_not_confirmed_for_this_boot') from None
