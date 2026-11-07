"""Russian operator CLI. Mutations require stopped bot and explicit confirmation."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
from .config import load_settings
from .files import private_dir, clean_task
from .state import Store, StateUnavailable

def confirm(text):
    print(text)
    return input('Введите yes для подтверждения [no]: ').strip() == 'yes'

def orphan_files(store,files_dir):
    active = store.rows("SELECT execution_id,ready_file FROM jobs WHERE phase NOT IN ('failed','cancelled','completed')")
    protected_ids = {str(r['execution_id']) for r in active if r['execution_id']}
    protected_ready = {Path(r['ready_file']).absolute() for r in active if r['ready_file']}
    candidates = []
    for p in Path('data/tmp').glob('*'):
        if p.name not in protected_ids and p.name.isdecimal() and not p.is_symlink():
            candidates.append(p)
    # Saved successful files are never orphan-cleanup candidates, even without DB rows.
    return candidates

def stopped_lock(component='bot'):
    private_dir('data')
    lock = Path(f'data/{component}.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise StateUnavailable(f'Сначала остановите {component}: сервис работает') from None
    return lock

def main():
    parser = argparse.ArgumentParser(description='Администрирование BotDownload v1')
    sub = parser.add_subparsers(dest='command',required=True)
    for name,desc in [('status','Общее состояние'),('queue','Очередь'),('disk','Свободный диск'),('cache','Состояние кэша'),('errors','Последние ошибки'),('doctor','Проверка конфигурации и зависимостей')]:
        sub.add_parser(name,help=desc,description=desc)
    temp = sub.add_parser('tempfiles',help='Безопасная очистка временных файлов').add_subparsers(dest='operation',required=True)
    temp.add_parser('list',help='Список сиротских каталогов')
    temp.add_parser('clean',help='Очистка после подтверждения').add_argument('--dry-run',action='store_true',help='Только показать кандидатов')
    dbsub = sub.add_parser('db',help='Резервные копии и восстановление').add_subparsers(dest='operation',required=True)
    for name in ('status','backups','check','backup','init'):
        dbsub.add_parser(name,help={'status':'Состояние базы','backups':'Список копий','check':'Проверить целостность','backup':'Создать проверенную копию','init':'Создать новую базу с подтверждением'}[name])
    restore = dbsub.add_parser('restore',help='Восстановить проверенную копию')
    restore.add_argument('--list',action='store_true',help='Показать копии')
    restore.add_argument('--backup',help='Имя копии внутри backups')
    args = parser.parse_args()
    cfg = load_settings(secrets=False).data
    store = Store()
    if args.command=='doctor':
        import importlib.metadata
        for binary in ('ffmpeg','ffprobe','deno'):
            import sys
            found = (Path(sys.executable).parent/'deno').is_file() if binary=='deno' else shutil.which(binary)
            print(binary, 'PASS' if found else 'FAIL')
        for package in ('aiogram','yt-dlp','PyYAML','python-dotenv'):
            print(package,importlib.metadata.version(package))
        print('Конфигурация PASS; защита worker требует отдельной фактической проверки на VPS.')
        return
    if args.command=='disk':
        print('Свободно байт:',shutil.disk_usage('.').free)
        return
    if args.command=='db' and args.operation in {'backups','check','restore'}:
        copies = sorted(Path('backups').glob('*.sqlite'),reverse=True)
        for p in copies:
            print(p.name, 'PASS' if store.verify(p) and store.compatible(p) else 'FAIL')
        if args.operation=='check':
            print('Рабочая база:', 'PASS' if store.verify(store.path) else 'FAIL')
        if args.operation!='restore' or args.list:
            return
    if args.command=='db' and args.operation in {'init','restore'}:
        with stopped_lock():
            if args.operation=='init':
                if not confirm('Новая база НЕ восстановит квоты, блокировки, кэш и очередь. Все существующие файлы базы будут сохранены отдельно.'):
                    return
                store.preserve_corrupt()
                # Explicit init permits creation after a prior unrecoverable corruption.
                store.path.touch(mode=0o600)
                store.initialize(cfg['backup'])
            else:
                if not args.backup or Path(args.backup).name != args.backup:
                    parser.error('Укажите имя копии без пути через --backup')
                p = Path('backups') / args.backup
                valid = store.verify(p) and store.compatible(p)
                print('Копия:',p.absolute(),'mtime:',p.stat().st_mtime if p.exists() else '-', 'PASS' if valid else 'FAIL')
                if not valid:
                    raise StateUnavailable('Копия повреждена/несовместима')
                if confirm('Восстановление потеряет изменения после даты копии. Рабочая база будет сохранена отдельно.'):
                    store.restore(p)
                    print('Восстановление PASS')
        return
    if not store.verify(store.path) or not store.compatible(store.path):
        raise StateUnavailable('База отсутствует, повреждена или требует миграции через запуск бота')
    if args.command=='db':
        if args.operation=='backup':
            print(store.backup(cfg['backup']['max_copies']))
        else:
            print('SQLite PASS')
    elif args.command in {'status','queue'}:
        query = 'SELECT phase,count(*) AS count FROM jobs GROUP BY phase' if args.command=='status' else "SELECT id,phase,queued FROM jobs WHERE phase='queued' ORDER BY queued,id"
        for r in store.rows(query):
            print(json.dumps(r,ensure_ascii=False))
    elif args.command=='cache':
        print(store.rows('SELECT valid,count(*) AS count FROM result_cache GROUP BY valid'))
    elif args.command=='errors':
        print(store.rows('SELECT id,error FROM jobs WHERE error IS NOT NULL ORDER BY id DESC LIMIT 20'))
    elif args.command=='tempfiles':
        # Hold bot lock during both discovery and deletion; no race with scheduling.
        with stopped_lock(), stopped_lock('worker'):
            candidates = orphan_files(store,cfg['storage']['files_dir'])
            for p in candidates:
                print(p)
            if args.operation=='clean' and not args.dry_run and confirm('Удалить только показанные сиротские временные каталоги?'):
                for p in candidates:
                    clean_task(int(p.name))

if __name__ == '__main__':
    try:
        main()
    except (StateUnavailable,ValueError,OSError) as exc:
        raise SystemExit(f'Ошибка: {exc}') from None
