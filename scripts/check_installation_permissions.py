"""Read-only installation permissions check; run as tgbot, never displays secrets."""
from pathlib import Path
import os
import stat
import sys


def check(path,kind,mode=None,required=True):
    try:
        info=path.lstat()
    except FileNotFoundError:
        print(('FAIL' if required else 'INCONCLUSIVE')+f': {path} — ещё не создан')
        return not required
    safe=not path.is_symlink() and info.st_uid==os.geteuid()
    safe=safe and (stat.S_ISDIR(info.st_mode) if kind=='dir' else stat.S_ISREG(info.st_mode))
    safe=safe and (stat.S_IMODE(info.st_mode)==mode if mode is not None else not info.st_mode & 0o022)
    print(('PASS' if safe else 'FAIL')+f': {path} — владелец/тип/права')
    return safe


def main():
    if os.geteuid()==0:
        raise SystemExit('Запустите эту проверку от имени tgbot, а не root.')
    if not Path('bot').is_dir() or not Path('config.yaml').is_file():
        raise SystemExit('Выполняйте из корня установленного проекта.')
    ok=check(Path('.'),'dir',0o700)
    for name in ('data','logs','backups','files','data/tmp'):
        ok=check(Path(name),'dir',0o700,required=name=='data') and ok
    for name in ('.env','config.yaml'):
        ok=check(Path(name),'file',0o600) and ok
    for path in sorted(Path('bot').glob('*.py')):
        ok=check(path,'file') and ok
    if not os.access('.venv/bin/python',os.X_OK):
        print('FAIL: .venv/bin/python — не исполняется')
        ok=False
    else:
        print('PASS: .venv/bin/python — доступен (symlink venv допустим)')
    print('Это только проверка файлов; сетевая изоляция и Telegram здесь не проверяются.')
    return 0 if ok else 1

if __name__=='__main__':
    raise SystemExit(main())
